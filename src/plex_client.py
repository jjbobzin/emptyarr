import requests
from typing import Optional, List, Dict


# Plex media type IDs
_MOVIE_TYPES = [1]           # movie
_TV_TYPES    = [2, 3, 4]     # show, season, episode
_MUSIC_TYPES = [8, 9, 10]    # artist, album, track


class PlexClient:
    def __init__(self, url: str, token: str):
        self.url   = url.rstrip("/")
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            "X-Plex-Token": token,
            "Accept":       "application/json",
        })

    def _get(self, path: str, params: dict = None, timeout: int = 15):
        return self.session.get(f"{self.url}{path}", params=params, timeout=timeout)

    def check_reachable(self) -> Dict:
        try:
            r = self._get("/identity")
            if r.status_code == 200:
                version = r.json().get("MediaContainer", {}).get("version", "?")
                return {"pass": True, "detail": f"Plex reachable (v{version}) at {self.url}"}
            return {"pass": False, "detail": f"Plex returned HTTP {r.status_code}"}
        except requests.exceptions.Timeout:
            return {"pass": False, "detail": f"Plex timed out: {self.url}"}
        except Exception as e:
            return {"pass": False, "detail": f"Plex unreachable ({self.url}): {e}"}

    def get_sections(self) -> List[Dict]:
        r = self._get("/library/sections")
        r.raise_for_status()
        return [
            {"id": str(s["key"]), "title": s["title"], "type": s["type"]}
            for s in r.json().get("MediaContainer", {}).get("Directory", [])
        ]

    def find_section_id(self, library_name: str) -> Optional[str]:
        try:
            for s in self.get_sections():
                if s["title"].lower() == library_name.lower():
                    return s["id"]
        except Exception:
            pass
        return None

    def get_section_type(self, section_id: str) -> str:
        """Return the section type string — 'movie', 'show', etc."""
        try:
            for s in self.get_sections():
                if s["id"] == section_id:
                    return s["type"]
        except Exception:
            pass
        return "movie"

    def get_library_item_count(self, section_id: str) -> int:
        try:
            r = self._get(f"/library/sections/{section_id}/all",
                          params={"X-Plex-Container-Start": 0,
                                  "X-Plex-Container-Size": 0})
            r.raise_for_status()
            return int(r.json().get("MediaContainer", {}).get("totalSize", 0))
        except Exception:
            return 0

    def _fetch_deleted(self, section_id: str, type_id: int) -> List[Dict]:
        """
        Fetch all items with deletedAt set for a given type.
        IMPORTANT: Token must be passed as query param (not header) for
        checkFiles=1 to include deletedAt on episode-level items in Plex.
        """
        try:
            # Use requests directly (not self.session) so token goes as query param
            r = requests.get(
                f"{self.url}/library/sections/{section_id}/all",
                params={
                    "checkFiles":    1,
                    "type":          type_id,
                    "X-Plex-Token":  self.token,
                },
                headers={"Accept": "application/json"},
                timeout=120,
            )
            if r.status_code != 200:
                return []
            items = r.json().get("MediaContainer", {}).get("Metadata", [])
            return [
                {
                    "title":      item.get("title", "Unknown"),
                    "year":       item.get("year", ""),
                    "type":       item.get("type", ""),
                    "deleted_at": item.get("deletedAt", 0),
                }
                for item in items if item.get("deletedAt")
            ]
        except Exception:
            return []

    def get_trash_items(self, section_id: str) -> List[Dict]:
        """
        Get items that will be removed by emptyTrash.
        Uses checkFiles=1 + deletedAt detection with pagination to handle
        large libraries. Queries all relevant type levels for TV/movie sections.
        """
        try:
            section_type = self.get_section_type(section_id)
            type_ids     = _TV_TYPES if section_type == "show" else _MOVIE_TYPES

            all_items  = []
            seen_titles = set()

            for type_id in type_ids:
                for item in self._fetch_deleted(section_id, type_id):
                    all_items.append(item)
                    seen_titles.add(item["title"])

            # Also check legacy trash=1 endpoint and merge
            try:
                r_legacy = self._get(
                    f"/library/sections/{section_id}/all",
                    params={"trash": 1},
                )
                if r_legacy.status_code == 200:
                    for item in r_legacy.json().get("MediaContainer", {}).get("Metadata", []):
                        if item.get("title") not in seen_titles:
                            all_items.append({
                                "title": item.get("title", "Unknown"),
                                "year":  item.get("year", ""),
                                "type":  item.get("type", ""),
                            })
            except Exception:
                pass

            return all_items
        except Exception:
            return []

    def empty_trash(self, section_id: str) -> Dict:
        try:
            r = self.session.put(
                f"{self.url}/library/sections/{section_id}/emptyTrash",
                timeout=30
            )
            if r.status_code in (200, 204):
                return {"ok": True,  "http": r.status_code}
            return {"ok": False, "http": r.status_code, "error": r.text[:200]}
        except Exception as e:
            return {"ok": False, "http": None, "error": str(e)}