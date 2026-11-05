#!/usr/bin/env python3
"""يحمّل كل الملفات الصوتية الظاهرة في صفحة ويب إلى مجلد واحد.

الاستخدام (على جهازك أنت):
    python3 download_audio.py "http://ruqayah.net/audio/index.php?catb=166&part=1" --out ~/Downloads/ruqayah
    python3 download_audio.py URL --pages          # يتبع روابط الصفحات التالية (part=2, part=3 ...)
    python3 download_audio.py URL --list           # يعرض الروابط فقط دون تحميل

بدون مكتبات خارجية. يتخطى الملفات الموجودة، ويضع فاصلاً بين الطلبات، ويحترم robots.txt.
للاستخدام الشخصي في الاستماع؛ التزم بشروط الموقع وحقوق أصحاب التسجيلات.
"""
import argparse
import html
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path

AUDIO_EXT = ('.mp3', '.m4a', '.ogg', '.wav', '.aac', '.wma', '.opus')
UA = 'tilawa-audio-downloader/1.0 (personal use)'
LINK = re.compile(r'''(?:href|src|data-[\w-]+|file|url)\s*[=:]\s*["']([^"'<>]+)["']''', re.I)
BARE = re.compile(r'''https?://[^\s"'<>]+''', re.I)


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        charset = r.headers.get_content_charset() or 'utf-8'
        return r.read().decode(charset, errors='replace')


def is_audio(url):
    path = urllib.parse.urlparse(url).path.lower()
    return path.endswith(AUDIO_EXT)


def extract(page_url, text):
    found = []
    for raw in LINK.findall(text) + BARE.findall(text):
        u = urllib.parse.urljoin(page_url, html.unescape(raw.strip()))
        if u.startswith(('http://', 'https://')) and u not in found:
            found.append(u)
    return found


def safe_name(url, used):
    name = urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name) or 'audio'
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip('. ') or 'audio'
    base, ext = os.path.splitext(name)
    n, out = 1, name
    while out in used:
        n += 1
        out = f'{base}_{n}{ext}'
    used.add(out)
    return out


def download(url, dest, timeout=60):
    tmp = dest.with_suffix(dest.suffix + '.part')
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, 'wb') as f:
        while chunk := r.read(1 << 16):
            f.write(chunk)
    tmp.replace(dest)


def main():
    ap = argparse.ArgumentParser(description='تحميل الملفات الصوتية من صفحة ويب')
    ap.add_argument('url')
    ap.add_argument('--out', default='audio-downloads', help='مجلد الحفظ')
    ap.add_argument('--delay', type=float, default=1.5, help='ثواني بين كل تحميل')
    ap.add_argument('--pages', action='store_true', help='اتبع روابط الصفحات التالية (part=)')
    ap.add_argument('--max-pages', type=int, default=30)
    ap.add_argument('--list', action='store_true', help='اعرض الروابط فقط')
    ap.add_argument('--ignore-robots', action='store_true')
    a = ap.parse_args()

    root = urllib.parse.urlparse(a.url)
    rp = urllib.robotparser.RobotFileParser()
    if not a.ignore_robots:
        try:
            rp.set_url(f'{root.scheme}://{root.netloc}/robots.txt')
            rp.read()
        except Exception:
            rp = None
    def allowed(u):
        return a.ignore_robots or rp is None or rp.can_fetch(UA, u)

    queue, seen_pages, audio = [a.url], set(), []
    while queue and len(seen_pages) < a.max_pages:
        page = queue.pop(0)
        if page in seen_pages:
            continue
        seen_pages.add(page)
        if not allowed(page):
            print(f'robots.txt يمنع: {page}', file=sys.stderr)
            continue
        try:
            text = fetch(page)
        except Exception as e:
            print(f'تعذر فتح {page}: {e}', file=sys.stderr)
            continue
        links = extract(page, text)
        got = [u for u in links if is_audio(u)]
        print(f'{page} -> {len(got)} ملف صوتي')
        audio += [u for u in got if u not in audio]
        if a.pages:
            for u in links:
                p = urllib.parse.urlparse(u)
                if p.netloc == root.netloc and p.path == root.path and 'part=' in p.query and u not in seen_pages:
                    queue.append(u)
        time.sleep(a.delay)

    if not audio:
        print('لم أجد روابط ملفات صوتية مباشرة. ابعث لي مصدر الصفحة (View page source) لأتعرف على نمط الروابط.', file=sys.stderr)
        return 1
    if a.list:
        print('\n'.join(audio))
        return 0

    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    used = {p.name for p in out.iterdir()}
    ok = skipped = failed = 0
    for i, url in enumerate(audio, 1):
        name = safe_name(url, set())
        dest = out / name
        if dest.exists() and dest.stat().st_size > 0:
            skipped += 1
            print(f'[{i}/{len(audio)}] موجود، تخطّي: {name}')
            continue
        try:
            download(url, dest)
            ok += 1
            print(f'[{i}/{len(audio)}] تم: {name}')
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            failed += 1
            print(f'[{i}/{len(audio)}] فشل: {name} ({e})', file=sys.stderr)
        time.sleep(a.delay)
    print(f'انتهى. نُزّل {ok}، تُخطّي {skipped}، فشل {failed}. المجلد: {out}')
    return 0 if not failed else 2


if __name__ == '__main__':
    sys.exit(main())
