import requests
import xml.etree.ElementTree as ET
from typing import Optional, List, Dict


# Plex media type IDs
_MOVIE_TYPES = [1]        # movie
_TV_TYPES    = [2, 3, 4]  # show, season, episode

# Human-readable labels for type IDs
_TYPE_LABELS = {
    1: "movie",
    2: "show",
    3: "season",
    4: "episode",
}


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
                                  "X-Plex-Container-Size":  0})
            r.raise_for_status()
            return int(r.json().get("MediaContainer", {}).get("totalSize", 0))
        except Exception:
            return 0

    def _quick_has_deleted(self, section_id: str) -> bool:
        """
        Fast check — uses JSON to see if ANY items have deletedAt set.
        JSON omits deletedAt on episode Media children but does include it
        on show/season level items. So if this returns True we definitely
        have deleted items; if False we still do the full XML check since
        episode-level deletions won't show here.
        Actually used to short-circuit: if JSON shows deletedAt on ANY
        top-level item, we know we need the full XML scan.
        We always do the XML scan — this just tells us we can skip it
        when the library is completely clean at show/season level AND
        we've recently confirmed no episode deletions.
        For now: always return True to always do full scan.
        Optimization opportunity: cache the last scan result.
        """
        return True  # Always do full scan for accuracy

    def _fetch_deleted_xml(self, section_id: str, type_id: int) -> List[Dict]:
        """
        Fetch items with deletedAt using XML (required — JSON omits deletedAt
        on Media children for episodes). Checks both item-level and
        Media child-level deletedAt.
        Returns list of {title, year, type, deleted_at, media_type_id}.
        """
        try:
            r = requests.get(
                f"{self.url}/library/sections/{section_id}/all",
                params={
                    "checkFiles":   1,
                    "type":         type_id,
                    "X-Plex-Token": self.token,
                },
                timeout=120,
            )
            if r.status_code != 200:
                return []
            root    = ET.fromstring(r.text)
            deleted = []
            for item in list(root):
                # Check deletedAt on the item itself (shows, seasons)
                if item.get("deletedAt"):
                    deleted.append({
                        "title":         item.get("title", "Unknown"),
                        "year":          item.get("year", ""),
                        "type":          _TYPE_LABELS.get(type_id, "item"),
                        "deleted_at":    int(item.get("deletedAt", 0)),
                        "media_type_id": type_id,
                    })
                else:
                    # Check deletedAt on <Media> children (episodes with
                    # unavailable/replaced file versions)
                    for media in item.findall("Media"):
                        if media.get("deletedAt"):
                            deleted.append({
                                "title":         item.get("title", "Unknown"),
                                "year":          item.get("year", ""),
                                "type":          _TYPE_LABELS.get(type_id, "item"),
                                "deleted_at":    int(media.get("deletedAt", 0)),
                                "media_type_id": type_id,
                            })
                            break  # one entry per episode
            return deleted
        except Exception:
            return []

    def get_trash_items(self, section_id: str) -> List[Dict]:
        """
        Get all items that will be removed by emptyTrash.
        Returns list of items with type info for breakdown reporting.
        """
        try:
            section_type = self.get_section_type(section_id)
            type_ids     = _TV_TYPES if section_type == "show" else _MOVIE_TYPES

            all_items   = []
            seen_titles = set()

            for type_id in type_ids:
                for item in self._fetch_deleted_xml(section_id, type_id):
                    # Deduplicate by title+type
                    key = f"{item['title']}_{item['type']}"
                    if key not in seen_titles:
                        all_items.append(item)
                        seen_titles.add(key)

            # Also check legacy trash=1 endpoint and merge
            try:
                r_legacy = self._get(
                    f"/library/sections/{section_id}/all",
                    params={"trash": 1},
                )
                if r_legacy.status_code == 200:
                    for item in r_legacy.json().get("MediaContainer", {}).get("Metadata", []):
                        key = f"{item.get('title', '')}_{item.get('type', '')}"
                        if key not in seen_titles:
                            all_items.append({
                                "title": item.get("title", "Unknown"),
                                "year":  item.get("year", ""),
                                "type":  item.get("type", ""),
                                "media_type_id": 0,
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