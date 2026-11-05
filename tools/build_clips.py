#!/usr/bin/env python3
"""يبني مقاطع الآيات لسورة (أو عدة سور) بتقطيع الوقفات ثم تحقق/تصحيح بالمحاذاة الصوتية مع مرجعين.

    python3 build_clips.py ~/Movies/ruqayah_audio --out ../audio/nakhli --surahs 112 67
    python3 build_clips.py ~/Movies/ruqayah_audio --out ../audio/nakhli --all --jobs 4

لكل حد بين آيتين:
- إن اتفق الحصري والمنشاوي على موضعه (فرق ≤ 0.8ث) وكان الحد الحالي قريبًا منه (≤ 0.9ث): يبقى.
- إن اتفق المرجعان وابتعد الحد الحالي: يُنقل إلى أقرب وقفة حقيقية قرب موضع المرجعين، ويُسجّل في fixed.
- إن اختلف المرجعان وأيّد أحدهما الحد الحالي: يبقى.
- غير ذلك: يُعلَّم unsure (والموقع يحذّر منه).
يكتب لكل سورة NNN.manifest.json (clips, lead, unsure, fixed, verified).
"""
import argparse
import json
import re
import sys
import tempfile
import traceback
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import split_ayahs as S  # noqa: E402
import split_all as SA  # noqa: E402
import refine as R  # noqa: E402

AGREE = 0.8     # اتفاق المرجعين
KEEP = 0.9      # قرب الحد الحالي من موضع المرجعين
SNAP = 1.1      # أقصى بعد عن موضع المرجعين للتثبيت على وقفة


def nearest_pause(cands, t, window):
    best = None
    for s, e, d in cands:
        mid = (s + e) / 2
        if abs(mid - t) <= window:
            score = d - 0.8 * abs(mid - t)
            if best is None or score > best[0]:
                best = (score, mid)
    return best[1] if best else None


def decide(cut, th, tm, cands):
    """يعيد (الحد النهائي، الحالة) حيث الحالة: keep / fixed / unsure."""
    agree = abs(th - tm) <= AGREE
    if agree:
        ref = (th + tm) / 2
        if abs(cut - ref) <= KEEP:
            return cut, 'keep'
        p = nearest_pause(cands, ref, SNAP)
        return (p, 'fixed') if p is not None else (ref, 'unsure')
    if abs(cut - th) <= KEEP or abs(cut - tm) <= KEEP:
        return cut, 'keep'
    return cut, 'unsure'


def build(args):
    surah, src, out = args
    out = Path(out)
    try:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            w24, w16 = td / 'a.wav', td / 'b.wav'
            S.decode(src, w24)
            R.to_wav16(src, w16)
            lead = SA.prior_lead(surah)
            res = {l: S.analyse(w24, surah, None, 0.35, l)[0] for l in (1, 2)}
            pick = SA.choose(res, lead)
            if not pick:
                return surah, 'no-pauses', None
            lead, best = pick
            words = S.load_words()[surah]
            n = len(words)
            levels = S.frame_levels(w24)
            total = len(levels) * S.FRAME
            sil, _ = S.find_silences(levels, best['db'], 0.2)
            cands = [(s, e, d) for s, e, d in sil if s > best['lo'] + 0.2 and e < best['hi'] - 0.2]
            bounds = [best['lo']] + list(best['cuts']) + [best['hi']]
            t_start = bounds[lead]
            ends = bounds[lead + 1:lead + n]          # نهايات الآيات 1..n-1
            status = ['keep'] * len(ends)
            verified = False
            if n >= 2:
                try:
                    x = R.read_wav(w16)
                    rr = R.analyse_surah(x, t_start, best['hi'], ends, ['husary', 'minshawi'], surah, str(td))
                    th, tm = rr['husary']['ends'], rr['minshawi']['ends']
                    for k in range(len(ends)):
                        ends[k], status[k] = decide(ends[k], th[k], tm[k], cands)
                    verified = True
                except Exception:
                    traceback.print_exc()
            # سلامة الترتيب
            prev = t_start
            for k in range(len(ends)):
                if ends[k] <= prev + 0.5:
                    status[k] = 'unsure'
                    ends[k] = max(ends[k], prev + 0.5)
                prev = ends[k]
            bounds[lead + 1:lead + n] = ends
            bounds[0] = max(0.0, bounds[0] - 0.15)
            bounds[-1] = min(total, bounds[-1] + 0.25)
            ay = [(bounds[lead + i], bounds[lead + i + 1]) for i in range(n)]
            if lead >= 1 and surah not in (1, 9):
                ay[0] = (bounds[lead - 1], ay[0][1])
            # علامات الشك: حد غير مؤكَّد، أو مقطع بخطأ إيقاع كبير
            errs = best['errs'][lead:]
            unsure = set(i + 2 for i, st in enumerate(status) if st == 'unsure')   # الآية التالية للحد تبدأ منه
            unsure |= set(i + 1 for i, st in enumerate(status) if st == 'unsure')  # والآية السابقة تنتهي عنده
            fixed = sorted(set(i + 1 for i, st in enumerate(status) if st == 'fixed'))
            if not verified:
                unsure |= set(i + 1 for i, e in enumerate(errs) if e > SA.UNSURE)
            unsure = sorted(u for u in unsure if 1 <= u <= n)
            out.mkdir(parents=True, exist_ok=True)
            manifest = []
            for i, (s_, e_) in enumerate(ay):
                name = f'{surah:03d}{i + 1:03d}'
                r = S.cut_clip(w24, s_, e_, out / f'{name}.m4a')
                if r.returncode:
                    return surah, 'cut-failed', None
                manifest.append({'file': f'{name}.m4a', 'start': round(s_, 3), 'end': round(e_, 3)})
            (out / f'{surah:03d}.manifest.json').write_text(json.dumps(
                {'source': src.name, 'surah': surah, 'ayahs': n, 'lead': lead, 'verified': verified,
                 'unsure': unsure, 'fixed': fixed, 'mean_error': round(best['mean'], 3), 'clips': manifest},
                ensure_ascii=False), encoding='utf-8')
            return surah, 'ok', {'unsure': len(unsure), 'fixed': len(fixed), 'verified': verified, 'ayahs': n}
    except Exception:
        traceback.print_exc()
        return surah, 'error', None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--out', required=True)
    ap.add_argument('--surahs', type=int, nargs='*')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--jobs', type=int, default=1)
    ap.add_argument('--skip-done', action='store_true', help='تخطّي السور التي لها manifest بحقل verified=true')
    a = ap.parse_args()
    folder = Path(a.folder).expanduser()
    files = {}
    for p in sorted(folder.iterdir()):
        m = re.match(r'(\d{1,3})_', p.name)
        if m and p.suffix.lower() in ('.mp3', '.m4a', '.wav'):
            files[int(m.group(1))] = p
    todo = sorted(files) if a.all else (a.surahs or [])
    out = Path(a.out).expanduser()
    if a.skip_done:
        def done(s):
            f = out / f'{s:03d}.manifest.json'
            try:
                return f.exists() and json.loads(f.read_text(encoding='utf-8')).get('verified')
            except Exception:
                return False
        todo = [s for s in todo if not done(s)]
    jobs = [(s, files[s], str(out)) for s in todo]
    # الأطول أولًا ليتوزع العمل
    jobs.sort(key=lambda j: -j[1].stat().st_size)
    results = []
    with ProcessPoolExecutor(a.jobs) as ex:
        for surah, st, info in ex.map(build, jobs):
            print(f'{surah:03d} {st} {info or ""}', flush=True)
            results.append((surah, st, info))
    bad = [r for r in results if r[1] != 'ok']
    print(f'انتهى: {len(results) - len(bad)} نجحت، {len(bad)} فشلت: {[r[0] for r in bad]}', flush=True)


if __name__ == '__main__':
    main()
