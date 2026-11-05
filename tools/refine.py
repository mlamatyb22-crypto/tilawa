#!/usr/bin/env python3
"""يضبط حدود الآيات في تلاوة سورة كاملة بمحاذاة صوتية مع تلاوة مرجعية للنص نفسه.

الفكرة: تلاوة مرجعية (الحصري، والمنشاوي للتحقق) معروف فيها بالضبط أين تنتهي كل آية، لأنها ملف لكل آية.
نحاذي الصوت (ميزات MFCC) زمنيًا بالبرمجة الديناميكية DTW في نطاق ضيق حول التقدير الأولي،
فنعرف أين تقابل نهاية كل آية مرجعية في تلاوة الشيخ. ثم نثبّت القص على أقرب وقفة حقيقية.
اتفاق المرجعين (الحصري والمنشاوي) على موضع الحد هو مقياس الثقة: إن اختلفا تُعلَّم الآية «غير مضمونة».

يعتمد على numpy و afconvert (macOS). ينزّل ملفات المرجعين إلى مجلد مؤقت ويحذفها بعد الاستعمال.
"""
import json
import math
import subprocess
import sys
import tempfile
import urllib.request
import wave
from pathlib import Path

import warnings

import numpy as np

warnings.filterwarnings('ignore', category=RuntimeWarning)  # تحذيرات Accelerate على macOS وهمية

SR = 16000
HOP = 640          # 40 ملي ثانية
NFFT = 1024
NMEL = 40
NMFCC = 13
FPS = SR / HOP     # 25 إطارًا في الثانية
BAND_S = 16.0      # نصف عرض نطاق المحاذاة بالثواني
API = 'https://api.alquran.cloud/v1/surah/%d/%s'
REFS = {'husary': 'ar.husary', 'minshawi': 'ar.minshawi'}


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def read_wav(path):
    with wave.open(str(path), 'rb') as w:
        assert w.getframerate() == SR and w.getnchannels() == 1
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def to_wav16(src, dst):
    r = run(['afconvert', '-f', 'WAVE', '-d', f'LEI16@{SR}', '-c', '1', str(src), str(dst)])
    if r.returncode:
        raise RuntimeError(r.stderr.strip())


def fetch_ref(surah, who, tmp):
    """يحمّل آيات السورة عند المرجع ويدمجها: (الصوت، حدود نهايات الآيات بالثواني)."""
    data = json.load(urllib.request.urlopen(API % (surah, REFS[who]), timeout=60))['data']['ayahs']
    chunks, ends, total = [], [], 0.0
    for i, a in enumerate(data):
        mp3 = Path(tmp) / f'{who}{i}.mp3'
        for attempt in range(3):
            try:
                with urllib.request.urlopen(a['audio'], timeout=60) as r:
                    mp3.write_bytes(r.read())
                break
            except Exception:
                if attempt == 2:
                    raise
        wav = Path(tmp) / f'{who}{i}.wav'
        to_wav16(mp3, wav)
        x = read_wav(wav)
        chunks.append(x)
        total += len(x) / SR
        ends.append(total)
        mp3.unlink()
        wav.unlink()
    return np.concatenate(chunks), ends


_MEL = None


def mel_bank():
    global _MEL
    if _MEL is None:
        def hz2mel(f):
            return 2595 * np.log10(1 + f / 700)
        def mel2hz(m):
            return 700 * (10 ** (m / 2595) - 1)
        pts = mel2hz(np.linspace(hz2mel(80), hz2mel(7600), NMEL + 2))
        bins = np.floor((NFFT + 1) * pts / SR).astype(int)
        fb = np.zeros((NMEL, NFFT // 2 + 1), dtype=np.float32)
        for m in range(1, NMEL + 1):
            l, c, r = bins[m - 1], bins[m], bins[m + 1]
            for k in range(l, c):
                fb[m - 1, k] = (k - l) / max(1, c - l)
            for k in range(c, r):
                fb[m - 1, k] = (r - k) / max(1, r - c)
        n = np.arange(NMEL)
        dct = np.cos(np.pi / NMEL * (n[None, :] + 0.5) * np.arange(NMFCC)[:, None]).astype(np.float32)
        _MEL = (fb, dct)
    return _MEL


def features(x):
    """MFCC (بدون c0) مع تطبيع المتوسط والتباين على السورة كلها لتقليل فرق الصوت بين القارئين."""
    fb, dct = mel_bank()
    n = 1 + max(0, (len(x) - NFFT)) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    fr = x[np.minimum(idx, len(x) - 1)] * np.hanning(NFFT).astype(np.float32)
    spec = np.abs(np.fft.rfft(fr, axis=1)) ** 2
    mel = np.log(spec @ fb.T + 1e-6)
    mf = (mel @ dct.T)[:, 1:]
    energy = mel.mean(axis=1)
    mf = (mf - mf.mean(0)) / (mf.std(0) + 1e-6)
    return mf.astype(np.float32), energy


def dtw_band(X, R, centre, band):
    """DTW بنمط خطوات (1،1) (1،2) (2،1) داخل نطاق حول centre[i]. يعيد مسارًا: لكل صف j في R زمن الصف i في X."""
    nx, nr = len(X), len(R)
    INF = np.float32(1e18)
    lo = np.clip(centre - band, 0, nr - 1).astype(np.int64)
    hi = np.clip(centre + band + 1, 1, nr).astype(np.int64)
    hi = np.maximum(hi, lo + 4)
    w = int((hi - lo).max())
    back = np.zeros((nx, w), dtype=np.int8)
    R2 = (R * R).sum(1)
    prev1 = (np.full(1, INF, dtype=np.float32), 0)   # (مصفوفة، بداية)
    prev2 = (np.full(1, INF, dtype=np.float32), 0)

    def get(prev, a, b):
        arr, st = prev
        out = np.full(b - a, INF, dtype=np.float32)
        s, e = max(a, st), min(b, st + len(arr))
        if e > s:
            out[s - a:e - a] = arr[s - st:e - st]
        return out

    for i in range(nx):
        a, b = int(lo[i]), int(hi[i])
        Rs = R[a:b]
        c = (X[i] @ X[i]) + R2[a:b] - 2 * (Rs @ X[i])
        c = np.sqrt(np.maximum(c, 0)).astype(np.float32)
        if i == 0:
            d = c.copy()
            d[:] = c
            # البداية حرّة داخل أول ثانيتين من المرجع
            free = (np.arange(a, b) < 2 * FPS)
            d = np.where(free, 2 * c, INF)
        else:
            c1 = get(prev1, a - 1, b - 1)      # (1،1): D[i-1, j-1]
            c2 = get(prev1, a - 2, b - 2)      # (1،2): D[i-1, j-2]
            c3 = get(prev2, a - 1, b - 1)      # (2،1): D[i-2, j-1]
            # أوزان متماثلة: (1،1)→2 و(1،2) و(2،1)→3 حتى لا ينحاز المسار لقفز الإطارات
            st = np.stack([c1 + 2 * c, c2 + 3 * c, c3 + 3 * c])
            k = st.argmin(0)
            d = st.min(0)
            back[i, :b - a] = k
        prev2 = prev1
        prev1 = (d, a)
    # نهاية المسار: أفضل نقطة قرب نهاية المرجع في آخر صف
    arr, st = prev1
    js_last = np.arange(st, st + len(arr))
    ok = js_last >= int(0.9 * nr)
    score = np.where(ok, arr / (nx + js_last), INF)
    j_end = int(np.argmin(score)) + st
    # تراجع
    path = []
    i, j = nx - 1, j_end
    while i >= 0 and j >= 0:
        path.append((i, j))
        if i == 0:
            break
        k = int(back[i, j - int(lo[i])])
        if k == 0:
            i, j = i - 1, j - 1
        elif k == 1:
            i, j = i - 1, j - 2
        else:
            i, j = i - 2, j - 1
        if j < 0 or j < int(lo[max(i, 0)]):
            break
    path.reverse()
    return path, float(arr[j_end - st]) / (nx + j_end)


def ref_boundaries_in_x(path, ref_ends, x0_frame):
    """لكل نهاية آية مرجعية (بالثواني) يعيد زمن المقابل في الصوت X (بالثواني، نسبةً لبداية X)."""
    js = np.array([p[1] for p in path])
    is_ = np.array([p[0] for p in path])
    out = []
    for e in ref_ends:
        j = e * FPS
        k = np.searchsorted(js, j)
        k = min(k, len(js) - 1)
        out.append(float(is_[k]) / FPS)
    return out


def analyse_surah(xwav16, t_start, t_end, approx_cuts, who_list, surah, tmp):
    """xwav16: صوت الشيخ كاملًا (مصفوفة). t_start: بداية جسم الآية الأولى بالثواني (بعد البسملة)، t_end نهاية الكلام.
    approx_cuts: تقدير أولي لنهايات الآيات (بالثواني) لضبط نطاق DTW. يعيد {who: [نهايات الآيات في صوت الشيخ]}."""
    s0 = max(0, int((t_start - 1.5) * SR))
    e0 = min(len(xwav16), int((t_end + 1.0) * SR))
    X, _ = features(xwav16[s0:e0])
    base = s0 / SR
    res = {}
    for who in who_list:
        ref, ends = fetch_ref(surah, who, tmp)
        R, _ = features(ref)
        # مركز النطاق: خطي بالمقاطع بين نهايات الآيات التقديرية ونهايات المرجع
        xs = np.array([t_start - 1.5 - base] + [c - base for c in approx_cuts] + [t_end + 1.0 - base]) * FPS
        rs = np.array([0.0] + list(ends[:-1]) + [ends[-1]]) * FPS
        rs[0] = 0.0
        centre = np.interp(np.arange(len(X)), xs, rs)
        path, cost = dtw_band(X, R, centre, int(BAND_S * FPS))
        pb = ref_boundaries_in_x(path, ends[:-1], 0)
        res[who] = {'ends': [base + t for t in pb], 'cost': cost, 'ref_secs': ends[-1]}
    return res
