"""
version_info.py
--------------------------------
Uygulama sürüm bilgisi. Elle sürüm numarası tutulmaz; git geçmişinden okunur:
  build = toplam commit sayısı, commit = kısa hash, date = son commit tarihi.
Git yoksa (ör. arşivden kurulum) "dev" döner. Süreç ömrü boyunca önbelleğe alınır;
yeni commit'ten sonra uygulamayı yeniden başlatmak yeterli.
"""

import subprocess
from functools import lru_cache
from pathlib import Path

REPO_URL = "https://github.com/siadyalidar/lal-commerce-os-"
_ROOT = Path(__file__).resolve().parent


def _git(*args):
    try:
        out = subprocess.run(
            ["git", *args], cwd=_ROOT, capture_output=True, text=True, timeout=2
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


@lru_cache(maxsize=1)
def get_version():
    commit = _git("rev-parse", "--short", "HEAD")
    if not commit:
        return {"label": "dev", "build": None, "commit": None, "date": None, "repo": REPO_URL}
    build = _git("rev-list", "--count", "HEAD") or None
    date = _git("log", "-1", "--format=%cs") or None
    dirty = "+" if _git("status", "--porcelain", "--untracked-files=no") else ""
    label = f"Sürüm {build} · {commit}{dirty}" if build else f"{commit}{dirty}"
    return {"label": label, "build": build, "commit": commit + dirty, "date": date, "repo": REPO_URL}
