#!/usr/bin/env python3
"""يقطّع مجلد سور كاملة (001_Al_Fatiha.mp3 ... 114_An_Nas.mp3) إلى آيات (m4a) على macOS.

    python3 split_all.py ~/Movies/ruqayah_audio --out ../dist/audio/nakhli
    python3 split_all.py ~/Movies/ruqayah_audio --only 112 113 114
    python3 split_all.py ~/Movies/ruqayah_audio --check-only

الاستخدام العملي: نمط المقاطع التمهيدية (بسملة/استعاذة) ثابت في التسجيلات من القارئ نفسه، فنأخذ
النمط الأشيع كمرجع ونفضّله عند تقارب النتائج، ونكتب report.csv ببيانات كل سورة. السور التي يبقى
خطؤها مرتفعًا لا تُقص، وتُدرج في التقرير للمراجعة. يستأنف العمل: السور المقطوعة سابقًا تُتخطى.
"""
import argparse
import csv
import json
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import split_ayahs as S  # noqa: E402

MAX_MEAN = 0.45
UNSURE = 0.9  # خطأ نسبي لمقطع واحد أعلى منه يُعلَّم «غير مضمون»


def prior_lead(s):
    """النمط المرصود في تسجيلات الشيخ حسن النخلي (فُحصت بداية كل ملف):
    الفاتحة والتوبة: مقطع تمهيدي واحد (البسملة هي الآية الأولى في الفاتحة، والتوبة بلا بسملة)،
    السور ١–٧٧: استعاذة ثم بسملة (مقطعان)، والسور ٧٨–١١٤: مقطع تمهيدي واحد."""
    if s in (1, 9):
        return 1
    return 2 if s <= 77 else 1



def choose(results, prior):
    """results: {lead: best-or-None}. نفضّل النمط المرجعي إن كان خطؤه مقبولًا وقريبًا من الأفضل."""
    ok = {k: v for k, v in results.items() if v}
    if not ok:
        return None
    best_lead = min(ok, key=lambda k: ok[k]['score'])
    if prior in ok and ok[prior]['mean'] <= MAX_MEAN and ok[prior]['score'] <= ok[best_lead]['score'] + 2.0:
        return prior, ok[prior]
    return best_lead, ok[best_lead]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('folder')
    ap.add_argument('--out', default='clips')
    ap.add_argument('--only', type=int, nargs='*')
    ap.add_argument('--check-only', action='store_true')
    ap.add_argument('--prior-lead', type=int, default=None, help='فرض النمط المرجعي (افتراضي: الأشيع تلقائيًا)')
    a = ap.parse_args()

    folder = Path(a.folder).expanduser()
    files = {}
    for p in sorted(folder.iterdir()):
        m = re.match(r'(\d{1,3})_', p.name)
        if m and p.suffix.lower() in ('.mp3', '.m4a', '.wav'):
            files[int(m.group(1))] = p
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    todo = [s for s in sorted(files) if 1 <= s <= 114 and (not a.only or s in a.only)]

    # المرحلة 1: تحليل كل سورة بالفرضيات 0/1/2
    info = {}
    for s in todo:
        if (out / f'{s:03d}.manifest.json').exists() and not a.check_only:
            print(f'{s:03d} مقطوعة سابقًا، تخطّي', flush=True)
            continue
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / 'f.wav'
            S.decode(files[s], wav)
            res = {}
            for lead in (1, 2):
                res[lead] = S.analyse(wav, s, None, 0.35, lead)[0]
        info[s] = res
        short = {k: (round(v['mean'], 2), round(v['score'], 2)) if v else None for k, v in res.items()}
        print(f'{s:03d} {files[s].name}: {short}', flush=True)

    leads = Counter(min((k for k, v in r.items() if v), key=lambda k: r[k]['score']) for r in info.values() if any(r.values()))
    print(f'توزيع الأفضل حسابيًا لكل سورة (للمقارنة بالنمط المرصود): {dict(leads)}', flush=True)

    rows = []
    for s in todo:
        if s not in info:
            continue
        pick = choose(info[s], prior_lead(s))
        if not pick:
            rows.append([s, files[s].name, '', '', '', 'لا وقفات كافية'])
            continue
        lead, best = pick
        status = 'جيد' if best['mean'] <= MAX_MEAN else 'خطأ مرتفع (لم تُقص)'
        if status == 'جيد' and not a.check_only:
            with tempfile.TemporaryDirectory() as td:
                wav = Path(td) / 'f.wav'
                S.decode(files[s], wav)
                total = len(S.frame_levels(wav)) * S.FRAME
                words = S.load_words()[s]
                bounds = [max(0.0, best['lo'] - 0.15)] + best['cuts'] + [min(total, best['hi'] + 0.25)]
                # النص المعروض للآية الأولى (من المصدر) يتضمن البسملة في كل السور عدا الفاتحة والتوبة؛
                # لذا يضم مقطع الآية الأولى آخر مقطع تمهيدي (البسملة)، وتُحذف الاستعاذة وأي تمهيد زائد.
                n_ay = len(words)
                ay = [(bounds[lead + i], bounds[lead + i + 1]) for i in range(n_ay)]
                if lead >= 1 and s not in (1, 9):
                    ay[0] = (bounds[lead - 1], ay[0][1])
                manifest = []
                errs = best['errs'][lead:]
                unsure = [i + 1 for i, e in enumerate(errs) if e > UNSURE]
                if lead >= 1 and s not in (1, 9) and len(best['errs']) > 0 and best['errs'][lead - 1] > UNSURE and (1 not in unsure):
                    unsure.append(1)
                for i, (st, en) in enumerate(ay):
                    name = f'{s:03d}{i + 1:03d}'
                    r = S.cut_clip(wav, st, en, out / f'{name}.m4a')
                    if r.returncode:
                        status = 'فشل القص'
                        break
                    manifest.append({'file': f'{name}.m4a', 'start': round(st, 3), 'end': round(en, 3)})
                else:
                    (out / f'{s:03d}.manifest.json').write_text(json.dumps(
                        {'source': files[s].name, 'surah': s, 'ayahs': len(words), 'lead': lead, 'unsure': sorted(unsure),
                         'mean_error': round(best['mean'], 3), 'clips': manifest}, ensure_ascii=False), encoding='utf-8')
                    status = 'تم القص'
        print(f'{s:03d} lead={lead} mean={best["mean"]:.2f} worst={best["worst"]:.2f} -> {status}', flush=True)
        rows.append([s, files[s].name, lead, f'{best["mean"]:.2f}', f'{best["worst"]:.2f}', status, len(unsure) if status == 'تم القص' else ''])

    with open(out / 'report.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(['surah', 'file', 'lead_clips', 'mean_error', 'worst_error', 'status', 'unsure_clips'])
        w.writerows(rows)
    bad = [r for r in rows if r[5] not in ('تم القص', 'جيد')]
    print(f'\nالمجموع {len(rows)} · تحتاج مراجعة: {len(bad)} · التقرير: {out / "report.csv"}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
