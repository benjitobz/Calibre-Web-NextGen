# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Chaptarr already holds curated metadata for the books it manages, resolved
# from its own upstream providers. Asking it directly keeps a library agreeing
# with the manager that fills it, instead of re-deriving the same books from a
# second source and drifting apart.
from typing import Dict, List, Optional, Union
from urllib.parse import quote

import requests

from cps import logger, config, constants
from cps.services.Metadata import MetaRecord, MetaSourceInfo, Metadata

log = logger.create()


class Chaptarr(Metadata):
    __name__ = "Chaptarr"
    __id__ = "chaptarr"
    DESCRIPTION = "Chaptarr"
    META_URL = "https://github.com/Chaptarr/chaptarr"

    TIMEOUT = 15
    MAX_RESULTS = 15

    def _endpoint(self) -> Optional[str]:
        base = (getattr(config, "config_chaptarr_url", None) or "").strip().rstrip("/")
        key = (getattr(config, "config_chaptarr_api_key", None) or "").strip()

        if not base or not key:
            return None

        if "://" not in base:
            base = "http://" + base

        return base

    def search(
        self, query: str, generic_cover: str = "", locale: str = "en"
    ) -> Optional[List[MetaRecord]]:
        if not self.active or not query:
            return None

        base = self._endpoint()

        if not base:
            return None

        key = (getattr(config, "config_chaptarr_api_key", None) or "").strip()

        try:
            response = requests.get(
                f"{base}/api/v1/book/lookup",
                params={"term": query},
                headers={
                    "X-Api-Key": key,
                    "User-Agent": getattr(constants, "USER_AGENT", "Calibre-Web-NextGen"),
                },
                timeout=self.TIMEOUT,
            )
            response.raise_for_status()
            results = response.json()
        except Exception as exc:
            log.warning("Chaptarr metadata lookup failed: %s", exc)
            return None

        if not isinstance(results, list):
            return None

        records = []

        for result in results[: self.MAX_RESULTS]:
            record = self._to_record(result, base, generic_cover)

            if record:
                records.append(record)

        return records

    def _to_record(self, result: dict, base: str, generic_cover: str) -> Optional[MetaRecord]:
        try:
            title = (result.get("title") or "").strip()

            if not title:
                return None

            editions = result.get("editions") or []
            edition = next((e for e in editions if e.get("monitored")), editions[0] if editions else {})

            author = result.get("author") or {}
            author_name = (author.get("authorName") or "").strip()

            identifiers: Dict[str, Union[str, int]] = {}
            self._add_identifier(identifiers, "goodreads", result.get("goodreadsBookId"))
            self._add_identifier(identifiers, "isbn", edition.get("isbn13"))

            asins = edition.get("asins") or []
            self._add_identifier(identifiers, "asin", edition.get("asin") or (asins[0] if asins else None))

            return MetaRecord(
                id=result.get("foreignBookId") or result.get("titleSlug") or title,
                title=title,
                authors=[author_name] if author_name else [],
                url=self._book_url(base, result),
                source=MetaSourceInfo(
                    id=self.__id__,
                    description=Chaptarr.DESCRIPTION,
                    link=Chaptarr.META_URL,
                ),
                cover=self._cover(result, edition, base) or generic_cover,
                description=result.get("overview") or edition.get("overview") or "",
                series=result.get("seriesTitle") or None,
                series_index=0,
                identifiers=identifiers,
                publisher=edition.get("publisher") or None,
                publishedDate=(result.get("releaseDate") or edition.get("releaseDate") or "")[:10] or None,
                rating=self._rating(result, edition),
                languages=[],
                tags=[t for t in (result.get("genres") or []) if t],
            )
        except Exception as exc:
            log.warning("Chaptarr metadata result could not be read: %s", exc)
            return None

    @staticmethod
    def _add_identifier(identifiers: dict, name: str, value) -> None:
        if not value:
            return

        # Chaptarr prefixes provider ids ("gr:61215351"); the bare id is what a
        # calibre identifier is expected to carry.
        text = str(value)
        identifiers[name] = text.split(":", 1)[1] if ":" in text else text

    @staticmethod
    def _book_url(base: str, result: dict) -> str:
        slug = result.get("titleSlug")
        return f"{base}/book/{quote(str(slug))}" if slug else base

    @staticmethod
    def _cover(result: dict, edition: dict, base: str) -> Optional[str]:
        for images in (result.get("images"), edition.get("images")):
            for image in images or []:
                url = image.get("url") or image.get("remoteUrl")

                if url:
                    return url if "://" in url else f"{base}{url}"

        remote = result.get("remoteCover")

        if remote:
            return remote if "://" in remote else f"{base}{remote}"

        return None

    @staticmethod
    def _rating(result: dict, edition: dict) -> int:
        for holder in (result, edition):
            value = (holder.get("ratings") or {}).get("value")

            if value:
                try:
                    return int(round(float(value)))
                except (TypeError, ValueError):
                    continue

        return 0
