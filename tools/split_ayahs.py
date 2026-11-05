#!/usr/bin/env python3
"""يقطّع تسجيل سورة كاملة إلى ملف لكل آية (m4a) على macOS بدون تثبيت شيء (يستخدم afconvert المدمج).

الطريقة:
1. يفك الصوت ويحسب مستوى كل 10 ملي ثانية، ويستخرج الوقفات (مناطق هادئة نسبةً لمستوى القراءة).
2. يجلب نص السورة العثماني ويحسب عدد كلمات كل آية (ويخزّنها في quran_words.json).
3. يختار نقاط القص من الوقفات بأسلوب البرمجة الديناميكية بحيث تتناسب أطوال المقاطع مع عدد كلمات
   كل آية. هذا يميّز الوقفة بين الآيات عن وقفة التنفس داخل الآية، ويكشف التسجيلات التي تحتوي
   استعاذة أو بسملة تمهيدية (تجرّب 0 و1 و2 مقطع تمهيدي وتختار الأقل خطأً).
4. يحسب خطأ التطابق ويرفض القص إن كان كبيرًا، ويكتب manifest.json بأزمنة كل آية.

    python3 split_ayahs.py 112_Al_Ikhlas.mp3 --surah 112 --out ~/tilawa-clips/nakhli
    python3 split_ayahs.py 112_Al_Ikhlas.mp3 --surah 112 --check-only

الأداة لا تعرف الكلمات المنطوقة؛ تقدّر المواضع من الإيقاع. لذلك تُفحص عينات بالسماع.
"""
import argparse
import audioop
import json
import math
import subprocess
import sys
import tempfile
import urllib.request
import wave
from pathlib import Path

RATE = 24000
FRAME = 0.01
HERE = Path(__file__).resolve().parent
WORDS_FILE = HERE / 'quran_words.json'
LEAD_WORDS = [4, 4]  # استعاذة ثم بسملة تقريبًا (عدد الكلمات)
BASMALA_WORDS = 4


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def load_words():
    """عدد كلمات كل آية بدون البسملة المدمجة في الآية الأولى. {surah: [n1, n2, ...]}"""
    if WORDS_FILE.exists():
        return {int(k): v for k, v in json.loads(WORDS_FILE.read_text(encoding='utf-8')).items()}
    print('أجلب نص القرآن مرة واحدة لحساب عدد الكلمات...', file=sys.stderr)
    with urllib.request.urlopen('https://api.alquran.cloud/v1/quran/quran-uthmani', timeout=60) as r:
        data = json.load(r)['data']['surahs']
    out = {}
    for s in data:
        counts = []
        for a in s['ayahs']:
            n = len(a['text'].split())
            if a['numberInSurah'] == 1 and s['number'] not in (1, 9) and a['text'].startswith('بِسْمِ'):
                n -= BASMALA_WORDS
            counts.append(n)
        out[s['number']] = counts
    assert sum(len(v) for v in out.values()) == 6236
    WORDS_FILE.write_text(json.dumps(out), encoding='utf-8')
    return out


def decode(src, wav):
    r = run(['afconvert', '-f', 'WAVE', '-d', f'LEI16@{RATE}', '-c', '1', str(src), str(wav)])
    if r.returncode:
        sys.exit('فشل فك الملف: ' + r.stderr.strip())


def frame_levels(wav):
    with wave.open(str(wav), 'rb') as w:
        n = int(RATE * FRAME)
        out = []
        while True:
            b = w.readframes(n)
            if len(b) < 2:
                break
            r = audioop.rms(b, 2)
            out.append(20 * math.log10(r / 32768) if r > 0 else -120.0)
    return out


def find_silences(levels, noise_db, min_len):
    if noise_db is None:
        srt = sorted(levels)
        loud = srt[int(len(srt) * 0.90)]
        noise_db = min(-20.0, max(-45.0, loud - 14))
    out, start = [], None
    for i, v in enumerate(levels + [0.0]):
        if v < noise_db:
            if start is None:
                start = i
        elif start is not None:
            d = (i - start) * FRAME
            if d >= min_len:
                out.append((start * FRAME, i * FRAME, d))
            start = None
    return out, noise_db


def align(cands, weights, lo, hi, durs=None):
    """يختار len(weights)-1 نقطة قص من cands (أزمنة) تقلل تباعد أطوال المقاطع عن المتوقع.
    lo/hi: بداية ونهاية الكلام الفعلي. يعيد (القصّات, متوسط الخطأ النسبي, أسوأ خطأ)."""
    k = len(weights)
    m = len(cands)
    if m < k - 1:
        return None
    speech = max(hi - lo, 1.0)
    per_word = speech / sum(weights)
    exp = [w * per_word for w in weights]

    def seg_cost(i, a, b):
        e = exp[i]
        return ((b - a - e) / (e + 0.6)) ** 2

    cum = 0.0
    cumexp = []
    for e in exp:
        cum += e
        cumexp.append(lo + cum)
    band = max(6.0, speech * 0.12)
    prev = {}
    for c in range(m):
        if abs(cands[c] - cumexp[0]) <= band:
            prev[c] = (seg_cost(0, lo, cands[c]), [c])
    for j in range(1, k - 1):
        cur = {}
        for c in range(m):
            if abs(cands[c] - cumexp[j]) > band:
                continue
            best = None
            for p, (cost, _) in prev.items():
                if p < c:
                    v = cost + seg_cost(j, cands[p], cands[c])
                    if best is None or v < best[0]:
                        best = (v, p)
            if best:
                cur[c] = (best[0], prev[best[1]][1] + [c])
        prev = cur
        if not prev:
            return None
    best = None
    for c, (cost, path) in prev.items():
        v = cost + seg_cost(k - 1, cands[c], hi)
        if best is None or v < best[0]:
            best = (v, path)
    if best is None:
        return None
    idx = best[1]
    cuts = [cands[c] for c in idx]
    bounds = [lo] + cuts + [hi]
    errs = [abs((b - a - exp[i]) / (exp[i] + 0.6)) for i, (a, b) in enumerate(zip(bounds[:-1], bounds[1:]))]
    score = best[0]
    if durs:
        # عقوبة على تجاهل وقفة قوية (طولها قريب من وقفات القص المختارة): غالبًا هي حد آية
        chosen = sorted(durs[c] for c in idx) or [0]
        strong = 0.75 * chosen[len(chosen) // 2]
        score += 0.6 * sum(1 for c in range(m) if c not in set(idx) and durs[c] >= strong)
    return cuts, sum(errs) / len(errs), max(errs), score


def cut_clip(wav, s, e, dest):
    with wave.open(str(wav), 'rb') as w:
        w.setpos(max(0, int(s * RATE)))
        frames = w.readframes(int((e - s) * RATE))
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / 'c.wav'
        with wave.open(str(tmp), 'wb') as o:
            o.setnchannels(1)
            o.setsampwidth(2)
            o.setframerate(RATE)
            o.writeframes(frames)
        return run(['afconvert', '-f', 'm4af', '-d', 'aac', '-b', '40000', str(tmp), str(dest)])


def analyse(wav, surah, noise, min_silence, force_lead=None):
    words = load_words()[surah]
    levels = frame_levels(wav)
    total = len(levels) * FRAME
    best = None
    for ms in (min_silence, 0.22, 0.15):
        sil, used_db = find_silences(levels, noise, ms)
        loud_idx = [i for i, v in enumerate(levels) if v >= used_db]
        if not loud_idx:
            continue
        lo, hi = loud_idx[0] * FRAME, (loud_idx[-1] + 1) * FRAME
        inner = [(s, e, d) for s, e, d in sil if s > lo + 0.3 and e < hi - 0.3]
        cands = [(s + e) / 2 for s, e, d in inner]
        durs = [d for s, e, d in inner]
        for lead in ([force_lead] if force_lead is not None else (0, 1, 2)):
            weights = LEAD_WORDS[:lead] + words
            res = align(cands, weights, lo, hi, durs)
            if res and (best is None or res[3] < best['score']):
                best = {'cuts': res[0], 'mean': res[1], 'worst': res[2], 'score': res[3], 'lead': lead, 'lo': lo, 'hi': hi,
                        'db': used_db, 'cands': len(cands), 'min_silence': ms}
        if best and best['mean'] < 0.35:
            break
    return best, total, words


def main():
    ap = argparse.ArgumentParser(description='تقطيع سورة إلى آيات بمطابقة الوقفات مع عدد الكلمات')
    ap.add_argument('input')
    ap.add_argument('--surah', type=int, required=True)
    ap.add_argument('--lead', type=int, default=None, help='فرض عدد المقاطع التمهيدية (افتراضي: اختيار تلقائي)')
    ap.add_argument('--out', default='clips')
    ap.add_argument('--noise', type=float, default=None, help='عتبة الصمت dB (افتراضي: تلقائي)')
    ap.add_argument('--min-silence', type=float, default=0.35)
    ap.add_argument('--max-mean-error', type=float, default=0.45, help='أكبر متوسط خطأ نسبي مقبول')
    ap.add_argument('--check-only', action='store_true')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    if not 1 <= a.surah <= 114:
        sys.exit('رقم السورة بين 1 و114')
    src = Path(a.input).expanduser()
    if not src.exists() or not Path('/usr/bin/afconvert').exists():
        sys.exit('الملف غير موجود أو afconvert غير متوفر (macOS فقط)')

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / 'full.wav'
        decode(src, wav)
        best, total, words = analyse(wav, a.surah, a.noise, a.min_silence, a.lead)
        n = len(words)
        print(f'{src.name}: المدة {total:.1f}ث · الآيات {n}')
        if not best:
            print('لم أجد وقفات كافية.', file=sys.stderr)
            return 1
        ok = best['mean'] <= a.max_mean_error
        print(f'تمهيدي={best["lead"]} · وقفات مرشحة={best["cands"]} · عتبة={best["db"]:.1f}dB · '
              f'متوسط الخطأ={best["mean"]:.2f} · الأسوأ={best["worst"]:.2f} · {"جيد" if ok else "مرتفع: راجع يدويًا"}')
        if a.check_only:
            return 0 if ok else 2
        if not ok and not a.force:
            print('توقفت دون قص (استخدم --force للقص ثم افحص).', file=sys.stderr)
            return 2
        bounds = [best['lo']] + best['cuts'] + [best['hi']]
        bounds[0] = max(0.0, bounds[0] - 0.15)
        bounds[-1] = min(total, bounds[-1] + 0.25)
        names = [f'{a.surah:03d}000{chr(97 + i)}' for i in range(best['lead'])] + [f'{a.surah:03d}{i + 1:03d}' for i in range(n)]
        out = Path(a.out).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        manifest = []
        for name, (s, e) in zip(names, zip(bounds[:-1], bounds[1:])):
            dest = out / f'{name}.m4a'
            r = cut_clip(wav, s, e, dest)
            if r.returncode:
                print(f'فشل قص {name}: {r.stderr.strip()}', file=sys.stderr)
                return 1
            manifest.append({'file': dest.name, 'start': round(s, 3), 'end': round(e, 3), 'seconds': round(e - s, 2)})
        (out / f'{a.surah:03d}.manifest.json').write_text(json.dumps(
            {'source': src.name, 'surah': a.surah, 'ayahs': n, 'lead': best['lead'],
             'mean_error': round(best['mean'], 3), 'clips': manifest}, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'تم: {len(manifest)} مقطعًا في {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
