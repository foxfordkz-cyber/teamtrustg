"""Async Jira REST API client with retries and circuit breaker.

ВАЖНО: Jira Cloud удалила старый /rest/api/2/search (HTTP 410).
Поиск мигрирован на новый /rest/api/3/search/jql (POST с телом запроса).
Остальные операции (issue, transitions, comment) работают на /rest/api/2.
"""
import base64
import json
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any

import aiohttp
from config import CONFIG

logger = logging.getLogger("teamtrustgate")

_NON_RETRYABLE_STATUSES = {400, 401, 403, 404, 410}

_PRIORITY_MAP = {
    "Highest": "Highest",
    "High":    "High",
    "Medium":  "Medium",
    "Low":     "Low",
}


class CircuitBreaker:
    FAILURE_THRESHOLD = 5
    OPEN_DURATION_MINUTES = 5

    def __init__(self):
        self._failure_count = 0
        self._open_until: Optional[datetime] = None

    def is_open(self) -> bool:
        if self._open_until is None:
            return False
        if datetime.now(timezone.utc) < self._open_until:
            return True
        self._reset()
        return False

    def record_failure(self):
        self._failure_count += 1
        if self._failure_count >= self.FAILURE_THRESHOLD:
            self._open_until = datetime.now(timezone.utc) + timedelta(
                minutes=self.OPEN_DURATION_MINUTES
            )
            logger.error(f"jira: circuit breaker ОТКРЫТ до {self._open_until.isoformat()}")

    def record_success(self):
        if self._failure_count > 0:
            logger.info("jira: circuit breaker сброшен после успешного запроса")
        self._reset()

    def _reset(self):
        self._failure_count = 0
        self._open_until = None


class JiraClient:
    def __init__(self):
        # base_url для операций над тикетами (issue, transitions, comment)
        self.base_url = f"{CONFIG.JIRA_URL}/rest/api/2"
        # base для поиска — новый эндпоинт v3 (старый v2/search удалён)
        self.search_base = f"{CONFIG.JIRA_URL}/rest/api/3"
        auth_str = f"{CONFIG.JIRA_EMAIL}:{CONFIG.JIRA_API_TOKEN}"
        self._headers = {
            "Authorization": "Basic " + base64.b64encode(auth_str.encode()).decode(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._circuit = CircuitBreaker()

    async def _request(
        self,
        method: str,
        endpoint: str,
        json_data: Optional[dict] = None,
        retries: int = 3,
        base: Optional[str] = None,
    ) -> tuple[int, str]:
        """
        Универсальный запрос к Jira.
        base — переопределение base_url (для поиска используем search_base).
        """
        if self._circuit.is_open():
            raise RuntimeError(
                "Jira circuit breaker активен — сервис временно недоступен. "
                "Попробуйте через несколько минут."
            )

        url = f"{base or self.base_url}{endpoint}"
        last_err: Optional[Exception] = None

        for attempt in range(retries):
            try:
                timeout = aiohttp.ClientTimeout(total=CONFIG.JIRA_TIMEOUT)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.request(
                        method, url, headers=self._headers, json=json_data
                    ) as resp:
                        text = await resp.text()

                        if resp.status in _NON_RETRYABLE_STATUSES:
                            raise RuntimeError(
                                f"Jira {resp.status}: {_status_message(resp.status, text)}"
                            )

                        if 500 <= resp.status < 600:
                            self._circuit.record_failure()
                            raise RuntimeError(
                                f"Jira server error {resp.status}: {text[:300]}"
                            )

                        self._circuit.record_success()
                        logger.debug(f"jira: {method} {endpoint} → {resp.status}")
                        return resp.status, text

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = e
                wait = 2 ** attempt
                logger.warning(
                    f"jira: попытка {attempt + 1}/{retries} провалилась "
                    f"({type(e).__name__}), ждём {wait}с"
                )
                await asyncio.sleep(wait)

            except RuntimeError:
                raise

        raise RuntimeError(f"Jira недоступна после {retries} попыток: {last_err}")

    async def _search(
        self,
        jql: str,
        fields: List[str],
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Выполняет JQL-поиск с поддержкой пагинации nextPageToken.

        max_results — максимальное общее количество тикетов,
        которое должен вернуть метод со всех страниц.
        """
        total_limit = max(0, int(max_results))

        if total_limit == 0:
            return []

        issues: List[Dict[str, Any]] = []

        next_page_token: Optional[str] = None
        seen_tokens: set[str] = set()

        # Не запрашиваем слишком большую страницу за один раз.
        page_size = min(total_limit, 100)

        while len(issues) < total_limit:
            remaining = total_limit - len(issues)

            payload: Dict[str, Any] = {
                "jql": jql,
                "fields": fields,
                "maxResults": min(page_size, remaining),
            }

            if next_page_token:
                payload["nextPageToken"] = next_page_token

            _, text = await self._request(
                "POST",
                "/search/jql",
                json_data=payload,
                base=self.search_base,
            )

            try:
                data = json.loads(text)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    "Jira вернула некорректный JSON "
                    "при выполнении JQL-поиска"
                ) from error

            page_issues = data.get("issues", [])

            if not isinstance(page_issues, list):
                raise RuntimeError(
                    "Jira вернула некорректное поле issues"
                )

            issues.extend(page_issues)

            is_last = bool(data.get("isLast", False))
            new_token = data.get("nextPageToken")

            logger.debug(
                "jira search: получено %s, всего %s/%s, isLast=%s",
                len(page_issues),
                len(issues),
                total_limit,
                is_last,
            )

            # Последняя страница или Jira не вернула следующий токен.
            if is_last or not new_token:
                break

            # Защита от бесконечного цикла,
            # если Jira повторно вернула тот же токен.
            if new_token in seen_tokens:
                logger.warning(
                    "jira search: повторный nextPageToken, "
                    "пагинация остановлена"
                )
                break

            # Пустая страница с токеном тоже не должна создавать цикл.
            if not page_issues:
                logger.warning(
                    "jira search: получена пустая страница, "
                    "пагинация остановлена"
                )
                break

            seen_tokens.add(new_token)
            next_page_token = new_token

        return issues[:total_limit]

    async def search_recent_issues(
        self,
        days: int = CONFIG.DEDUP_DAYS,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """Недавние открытые тикеты проекта для дедупликации."""
        jql = (
            f"project={CONFIG.JIRA_PROJECT_KEY} "
            f"AND created >= -{days}d "
            f"AND status != Done "
            f"ORDER BY created DESC"
        )
        issues = await self._search(jql, ["summary", "description", "key"], max_results)
        result = [
            {
                "key": i["key"],
                "summary": i["fields"].get("summary", ""),
                "description": self._extract_desc(i["fields"].get("description")),
            }
            for i in issues
        ]
        logger.info(f"jira: найдено {len(result)} тикетов за последние {days} дней")
        return result

    async def search_recent_project_issues(
        self,
        max_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Возвращает последние тикеты всего проекта.

        Используется администратором в команде /list.
        """
        safe_limit = max(1, min(int(max_results), 50))

        jql = (
            f"project={CONFIG.JIRA_PROJECT_KEY} "
            f"ORDER BY created DESC"
        )

        issues = await self._search(
            jql,
            ["summary", "status", "priority", "key"],
            safe_limit,
        )

        result = self._format_issue_list(issues)

        logger.info(
            "jira: найдено %s последних тикетов проекта",
            len(result),
        )
        return result

    async def search_issues_by_keys(
        self,
        issue_keys: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Возвращает данные только по переданным ключам тикетов.

        Используется для отображения собственных тикетов сейлза.
        """
        clean_keys = [
            str(key).strip().upper()
            for key in issue_keys
            if key and str(key).strip()
        ]

        if not clean_keys:
            return []

        # Убираем повторы, сохраняя первоначальный порядок.
        clean_keys = list(dict.fromkeys(clean_keys))[:50]

        quoted_keys = ", ".join(
            f'"{key}"'
            for key in clean_keys
        )

        jql = (
            f"project={CONFIG.JIRA_PROJECT_KEY} "
            f"AND key IN ({quoted_keys})"
        )

        issues = await self._search(
            jql,
            ["summary", "status", "priority", "key"],
            len(clean_keys),
        )

        result = self._format_issue_list(issues)

        # Jira может вернуть элементы в другом порядке.
        # Восстанавливаем порядок, полученный из SQLite.
        order = {
            key: index
            for index, key in enumerate(clean_keys)
        }

        result.sort(
            key=lambda issue: order.get(
                issue.get("key", ""),
                len(order),
            )
        )

        logger.info(
            "jira: найдено %s пользовательских тикетов",
            len(result),
        )
        return result

    def _format_issue_list(
        self,
        issues: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Преобразует Jira issues в единый формат для Telegram."""
        result: List[Dict[str, Any]] = []

        for issue in issues:
            fields = issue.get("fields") or {}
            status_data = fields.get("status") or {}
            priority_data = fields.get("priority") or {}

            result.append(
                {
                    "key": issue.get("key", ""),
                    "summary": fields.get("summary", ""),
                    "status": status_data.get("name", "—"),
                    "priority": priority_data.get("name", "Low"),
                }
            )

        return result

    async def get_project_stats(self, days: int = 30) -> Dict[str, Any]:
        """Статистика по тикетам проекта из Jira."""
        jql = (
            f"project={CONFIG.JIRA_PROJECT_KEY} "
            f"AND created >= -{days}d "
            f"ORDER BY created DESC"
        )
        issues = await self._search(jql, ["priority", "status", "created"], 200)
        total = len(issues)

        by_priority: Dict[str, int] = {}
        by_status:   Dict[str, int] = {}
        done_count = 0

        for i in issues:
            p = (i["fields"].get("priority") or {}).get("name", "Low")
            s = (i["fields"].get("status")   or {}).get("name", "—")
            by_priority[p] = by_priority.get(p, 0) + 1
            by_status[s]   = by_status.get(s, 0) + 1
            if s.lower() in ("done", "готово", "closed", "resolved"):
                done_count += 1

        return {
            "total":       total,
            "by_priority": by_priority,
            "by_status":   by_status,
            "done":        done_count,
            "in_progress": by_status.get("In Progress", 0) + by_status.get("В работе", 0),
        }

    async def export_issues(self, days: int = 90, max_results: int = 200) -> List[Dict[str, Any]]:
        """Выгружает все тикеты проекта за период для CSV экспорта."""
        jql = (
            f"project={CONFIG.JIRA_PROJECT_KEY} "
            f"AND created >= -{days}d "
            f"ORDER BY created DESC"
        )
        issues = await self._search(
            jql, ["summary", "status", "priority", "created", "updated", "labels"], max_results
        )
        result = []
        for i in issues:
            f = i["fields"]
            result.append({
                "key":      i["key"],
                "summary":  f.get("summary", ""),
                "status":   (f.get("status") or {}).get("name", ""),
                "priority": (f.get("priority") or {}).get("name", ""),
                "created":  (f.get("created") or "")[:10],
                "updated":  (f.get("updated") or "")[:10],
                "labels":   ", ".join(f.get("labels", [])),
            })
        logger.info(f"jira: экспортировано {len(result)} тикетов за {days} дней")
        return result

    async def search_sla_candidates(self, max_results: int = 100) -> List[Dict[str, Any]]:
        """
        Открытые (не закрытые) тикеты проекта для SLA-проверки.
        Возвращает приоритет, статус, дату создания и дедлайн (duedate).
        """
        jql = (
            f"project={CONFIG.JIRA_PROJECT_KEY} "
            f"AND statusCategory != Done "
            f"ORDER BY created ASC"
        )
        issues = await self._search(
            jql, ["summary", "status", "priority", "created", "duedate"], max_results
        )
        result = []
        for i in issues:
            f = i["fields"]
            result.append({
                "key":      i["key"],
                "summary":  f.get("summary", ""),
                "status":   (f.get("status") or {}).get("name", ""),
                "priority": (f.get("priority") or {}).get("name", "Low"),
                "created":  (f.get("created") or "")[:10],
                "duedate":  f.get("duedate"),  # YYYY-MM-DD или None
            })
        logger.info(f"jira: SLA-проверка — {len(result)} открытых тикетов")
        return result

    async def create_issue(
        self,
        summary: str,
        description: str,
        priority: str,
        labels: list,
        due_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        jira_priority = _PRIORITY_MAP.get(priority, "Low")
        fields = {
            "project":     {"key": CONFIG.JIRA_PROJECT_KEY},
            "summary":     summary[:255],
            "description": description,
            "issuetype":   {"name": "Story"},
            "priority":    {"name": jira_priority},
            "labels":      labels,
        }
        # Срок исполнения — нативное поле Jira duedate (формат YYYY-MM-DD)
        if due_date:
            fields["duedate"] = due_date
        payload = {"fields": fields}
        status, text = await self._request("POST", "/issue", payload)
        data = json.loads(text)
        key  = data.get("key")
        url  = f"{CONFIG.JIRA_URL}/browse/{key}"
        logger.info(f"jira: тикет создан {key}" + (f" (due {due_date})" if due_date else ""))
        return {"key": key, "url": url}

    async def delete_issue(
        self,
        issue_key: str,
    ) -> None:
        """Удаляет тикет из Jira."""
        await self._request(
            "DELETE",
            f"/issue/{issue_key}",
        )

        logger.info(
            "jira: тикет удалён %s",
            issue_key,
        )

    async def add_comment(self, issue_key: str, comment: str) -> None:
        await self._request("POST", f"/issue/{issue_key}/comment", {"body": comment})
        logger.info(f"jira: комментарий добавлен к {issue_key}")

    def _extract_desc(self, desc) -> str:
        if not desc:
            return ""
        if isinstance(desc, str):
            return desc
        if isinstance(desc, dict):
            return json.dumps(desc, ensure_ascii=False)
        return str(desc)


def _status_message(status: int, text: str) -> str:
    messages = {
        400: f"Некорректный запрос: {text[:300]}",
        401: "Ошибка авторизации — проверьте JIRA_EMAIL и JIRA_API_TOKEN",
        403: "Нет прав доступа — проверьте права токена в Jira",
        404: "Ресурс не найден — проверьте JIRA_URL и JIRA_PROJECT_KEY",
        410: f"API endpoint удалён: {text[:300]}",
    }
    return messages.get(status, text[:300])


JIRA_CLIENT = JiraClient()
