import cv2, numpy as np, statistics, time
from rapidocr_onnxruntime import RapidOCR

_ENGINE = None
ENGINE_LOAD_SECONDS = None      # debug: one-off model load cost
LAST_ELAPSE = None              # debug: [detect, classify, recognize] seconds of the last call

def _engine():
    global _ENGINE, ENGINE_LOAD_SECONDS
    if _ENGINE is None:
        t0 = time.perf_counter()
        _ENGINE = RapidOCR()          # models ship inside the wheel -> fully offline
        ENGINE_LOAD_SECONDS = time.perf_counter() - t0
        # print(f"[ocr-debug] RapidOCR engine loaded in {ENGINE_LOAD_SECONDS:.2f}s (one-off per process)", flush=True)
    return _ENGINE

def estimate_skew(gray, max_deg=5.0, step=0.5):
    scale = 800 / gray.shape[1]
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    bw = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    h, w = bw.shape
    best, best_a = -1.0, 0.0
    for a in np.arange(-max_deg, max_deg + step, step):
        M = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0)
        r = cv2.warpAffine(bw, M, (w, h), flags=cv2.INTER_NEAREST)
        s = float(np.var(r.sum(axis=1)))
        if s > best:
            best, best_a = s, float(a)
    return best_a

def deskew(gray):
    a = estimate_skew(gray)
    if abs(a) < 0.5:
        return gray
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0)
    return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)

def rapid_ocr(gray):
    """gray: 2-D uint8 array. Returns (text, mean_conf_0_100, n_words)."""
    global LAST_ELAPSE
    res, el = _engine()(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
    LAST_ELAPSE = el
    if not res:
        return "", 0.0, 0
    items = []
    for box, txt, conf in res:
        xs = [p[0] for p in box]; ys = [p[1] for p in box]
        items.append({"x0": min(xs), "x1": max(xs), "yc": (min(ys) + max(ys)) / 2,
                      "h": max(ys) - min(ys), "t": txt.strip(), "c": float(conf)})
    items = [i for i in items if i["t"]]
    if not items:
        return "", 0.0, 0
    med_h = statistics.median(i["h"] for i in items)
    items.sort(key=lambda i: i["yc"])
    rows = []
    for it in items:
        if rows and abs(it["yc"] - rows[-1]["yc"]) <= 0.6 * med_h:
            rows[-1]["items"].append(it)
            rows[-1]["yc"] = sum(i["yc"] for i in rows[-1]["items"]) / len(rows[-1]["items"])
        else:
            rows.append({"yc": it["yc"], "items": [it]})
    lines = []
    for r in rows:
        r["items"].sort(key=lambda i: i["x0"])
        parts, prev = [], None
        for it in r["items"]:
            if prev is not None:
                parts.append("  " if it["x0"] - prev > 2 * med_h else " ")
            parts.append(it["t"]); prev = it["x1"]
        lines.append("".join(parts))
    conf = 100 * sum(i["c"] for i in items) / len(items)
    return "\n".join(lines), conf, len(items)