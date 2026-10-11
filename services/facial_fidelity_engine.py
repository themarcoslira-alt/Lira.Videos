# -*- coding: utf-8 -*-
"""
services/facial_fidelity_engine.py — ANTIGRAVITY #3 (visão computacional)

Compara a FACE da imagem gerada (cena avatar) com a reference.png do personagem
(@Marcos) e devolve um índice de "fidelidade facial" (0-100).

- Se os modelos reais de face estiverem disponíveis (face_recognition + opencv),
  usa embeddings de face (cosine similarity) — caminho "embeddings".
- Fallback determinístico (Pillow + numpy, sem dependências extras): segmentação
  de pele (YCrCb), maior região conectada como candidata a rosto na imagem
  gerada, e comparação da região com o crop facial da referência via correlação
  estrutural + interseção de histogramas (Y/Cr/Cb).

Limitação documentada: sem um modelo de reconhecimento facial treinado, o
fallback é um PROXY perceptual (tom de pele + estrutura da região facial). Ele
é conservador o bastante para rejeitar erros grosseiros (rosto ausente, tom de
pele muito distinto, composição quebrada), que é o objetivo do fluxo de rejeição
com fidelidade < 70%.
"""
from pathlib import Path
from typing import Dict, Optional

import numpy as np

try:
    from PIL import Image, ImageOps
    _PIL_OK = True
except Exception:  # pragma: no cover
    _PIL_OK = False

LIMIAR_FIDELIDADE_AVATAR = 70

# Cada unidade de distância no espaço de croma (Cb,Cr) entre a pele da referência
# e da imagem gerada reduz o score em ESCALA_DISTANCIA_CROMA pontos.
ESCALA_DISTANCIA_CROMA = 3.0


def _face_libs_disponiveis() -> bool:
    """True quando face_recognition + opencv estão instalados (embeddings reais)."""
    import importlib.util
    return bool(importlib.util.find_spec("face_recognition") and importlib.util.find_spec("cv2"))


def _cv2_disponivel() -> bool:
    """True quando opencv está instalado (detecção Haar + correlação)."""
    import importlib.util
    return bool(importlib.util.find_spec("cv2"))


# ---------------------------------------------------------------------------
# Utilidades de imagem
# ---------------------------------------------------------------------------

def _carregar_rgb(caminho: str) -> Optional[np.ndarray]:
    """Lê a imagem e devolve np.uint8 HxWx3 RGB (ou None em qualquer erro)."""
    if not _PIL_OK:
        return None
    try:
        with Image.open(caminho) as im:
            im = ImageOps.exif_transpose(im)
            arr = np.asarray(im.convert("RGB"), dtype=np.uint8)
            return arr if arr.ndim == 3 else None
    except Exception:
        return None


def _mascara_pele_ycrcb(rgb: np.ndarray) -> np.ndarray:
    """Máscara booleana de pele a partir do espaço YCbCr (PIL: canais Y,Cb,Cr)."""
    if rgb is None:
        return np.zeros((1, 1), dtype=bool)
    try:
        im = Image.fromarray(rgb).convert("YCbCr")
        ycc = np.asarray(im, dtype=np.int16)
    except Exception:
        return np.zeros((1, 1), dtype=bool)
    y, cb, cr = ycc[..., 0], ycc[..., 1], ycc[..., 2]
    # Rangos clássicos de pele (JPEG YCbCr): Y>60, Cb 77-127, Cr 133-180.
    return (y > 60) & (cb >= 77) & (cb <= 130) & (cr >= 133) & (cr <= 180)


def _maior_regiao_conectada(mask: np.ndarray) -> np.ndarray:
    """Devolve a máscara (mesmo shape) só da maior componente conexa (BFS)."""
    if mask is None or mask.size == 0 or not mask.any():
        return np.zeros_like(mask, dtype=bool)
    from collections import deque
    h, w = mask.shape
    visitado = np.zeros_like(mask, dtype=bool)
    melhor = np.zeros_like(mask, dtype=bool)
    melhor_n = 0
    for i in range(h):
        for j in range(w):
            if mask[i, j] and not visitado[i, j]:
                comp = np.zeros_like(mask, dtype=bool)
                fila = deque([(i, j)])
                visitado[i, j] = True
                n = 0
                while fila:
                    x, y = fila.popleft()
                    if not mask[x, y] or comp[x, y]:
                        continue
                    comp[x, y] = True
                    n += 1
                    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < h and 0 <= ny < w and mask[nx, ny] and not visitado[nx, ny]:
                            visitado[nx, ny] = True
                            fila.append((nx, ny))
                if n > melhor_n:
                    melhor_n = n
                    melhor = comp
    return melhor


def _bbox_regiao(mask: np.ndarray):
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def _downscale_mask(mask: np.ndarray, largura: int = 96):
    if mask is None or mask.size == 0:
        return mask, 1.0, 1.0
    h, w = mask.shape
    fator = largura / float(w)
    nh = max(2, int(round(h * fator)))
    try:
        im = Image.fromarray((mask.astype(np.uint8)) * 255).resize((largura, nh), Image.BILINEAR)
        down = np.asarray(im, dtype=np.uint8) > 127
    except Exception:
        return mask, 1.0, 1.0
    return down, w / float(largura), h / float(nh)


def _crop_face_generated(rgb: np.ndarray):
    """Localiza a maior região de pele (provável rosto/personagem) e recorta.

    Devolve (crop_rgb, ok) — ok=False quando não há pele detectável.
    """
    mask = _mascara_pele_ycrcb(rgb)
    if mask is None or mask.sum() < max(80, mask.size * 0.001):
        return None, False
    down, sx, sy = _downscale_mask(mask)
    down = _maior_regiao_conectada(down)
    if down.sum() < 4:
        return None, False
    bbox = _bbox_regiao(down)
    if not bbox:
        return None, False
    x0, y0, x1, y1 = (int(v) for v in bbox)
    ox0, oy0 = int(x0 * sx), int(y0 * sy)
    ox1, oy1 = int((x1 + 1) * sx), int((y1 + 1) * sy)
    h, w = rgb.shape[:2]
    margem_x = int((ox1 - ox0) * 0.10)
    cx0 = max(0, ox0 - margem_x)
    cx1 = min(w, ox1 + margem_x)
    # recorte justo na região de pele (pouca margem → histograma foca no tom de pele)
    cy1 = min(h, oy1)
    cy0 = max(0, oy0)
    if cy1 - cy0 < 12 or cx1 - cx0 < 12:
        return None, False
    return rgb[cy0:cy1, cx0:cx1], True


def _crop_face_referencia(rgb: np.ndarray):
    """A reference.png é um retrato centralizado — recorta a faixa central da face."""
    if rgb is None:
        return None
    h, w = rgb.shape[:2]
    x0, x1 = int(w * 0.20), int(w * 0.80)
    y0, y1 = int(h * 0.15), int(h * 0.65)
    if x1 - x0 < 12 or y1 - y0 < 12:
        return rgb
    return rgb[y0:y1, x0:x1]


# ---------------------------------------------------------------------------
# Métricas de similaridade
# ---------------------------------------------------------------------------

def _corr_estrutural(a: np.ndarray, b: np.ndarray) -> float:
    """Correlação normalizada (Pearson) entre os tons de cinza achatados."""
    try:
        av = a.ravel().astype(np.float64)
        bv = b.ravel().astype(np.float64)
        if av.std() < 1e-6 or bv.std() < 1e-6:
            return 0.0
        r = np.corrcoef(av, bv)[0, 1]
        return float(np.clip(r, 0.0, 1.0))
    except Exception:
        return 0.0


def _inter_histogramas(a: np.ndarray, b: np.ndarray, bins: int = 32) -> float:
    try:
        ha, _ = np.histogram(a.ravel(), bins=bins, range=(0, 256))
        hb, _ = np.histogram(b.ravel(), bins=bins, range=(0, 256))
        if ha.sum() == 0 or hb.sum() == 0:
            return 0.0
        return float(np.minimum(ha, hb).sum() / float(max(ha.sum(), hb.sum())))
    except Exception:
        return 0.0


def _crop_to_ycc(crop_rgb: np.ndarray) -> np.ndarray:
    """Redimensiona o crop para 64x64 e converte para YCrCb (uint8)."""
    if crop_rgb is None:
        raise ValueError("crop vazio")
    im = Image.fromarray(crop_rgb).resize((64, 64), Image.BILINEAR).convert("YCbCr")
    return np.asarray(im, dtype=np.uint8)


def _inter_contagens(h1: np.ndarray, h2: np.ndarray) -> float:
    """Interseção de histogramas já computados (mesmos bins)."""
    try:
        s1 = int(h1.sum())
        s2 = int(h2.sum())
        if s1 == 0 or s2 == 0:
            return 0.0
        return float(np.minimum(h1, h2).sum() / float(max(s1, s2)))
    except Exception:
        return 0.0


def _hist_pele_crop(crop_rgb: np.ndarray, bins: int = 8):
    """Histogramas de Cb/Cr SOMENTE nos pixels de pele do crop.

    Retorna (cb_hist, cr_hist, frac_pele) ou None se não houver pele suficiente.
    """
    try:
        mask = _mascara_pele_ycrcb(crop_rgb)
        if mask is None or mask.sum() < 20:
            return None
        ycc = np.asarray(Image.fromarray(crop_rgb).convert("YCbCr"), dtype=np.uint8)
        cb = ycc[..., 1][mask]
        cr = ycc[..., 2][mask]
        frac = float(mask.sum()) / float(mask.size)
        h_cb, _ = np.histogram(cb, bins=bins, range=(0, 256))
        h_cr, _ = np.histogram(cr, bins=bins, range=(0, 256))
        return h_cb, h_cr, frac
    except Exception:
        return None


def _stats_pele(crop_rgb: np.ndarray):
    """Medianas de Cb/Cr dos pixels de pele do crop (assinatura de tom de pele).

    Retorna (median_cb, median_cr) ou None se não houver pele suficiente.
    """
    try:
        mask = _mascara_pele_ycrcb(crop_rgb)
        if mask is None or mask.sum() < 20:
            return None
        ycc = np.asarray(Image.fromarray(crop_rgb).convert("YCbCr"), dtype=np.float64)
        return (float(np.median(ycc[..., 1][mask])),
                float(np.median(ycc[..., 2][mask])))
    except Exception:
        return None


def _similaridade_crops(crop_ref: np.ndarray, crop_gen: np.ndarray) -> float:
    """Pontua 0-100 pela distância do TOM DE PELE (mediana Cb/Cr) dos crops.

    Medianas são robustas a bordas/fundo/pose. Quanto maior a distância no espaço
    de croma (Cb, Cr), menor a fidelidade facial (proxy de identidade).
    """
    try:
        a = _stats_pele(crop_ref)
        b = _stats_pele(crop_gen)
        if a is None or b is None:
            return 0.0
        dist = float(np.hypot(a[0] - b[0], a[1] - b[1]))
        score = 100.0 - ESCALA_DISTANCIA_CROMA * dist
        return float(np.clip(score, 0.0, 100.0))
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Caminho com embeddings reais (opcional — exige face_recognition + opencv)
# ---------------------------------------------------------------------------

def _fidelidade_por_embeddings(ref_path: str, gen_path: str) -> Optional[Dict]:
    try:
        import importlib.util
        if not (importlib.util.find_spec("face_recognition") and importlib.util.find_spec("cv2")):
            return None
        import cv2
        import face_recognition
        ref = cv2.imread(ref_path)
        gen = cv2.imread(gen_path)
        if ref is None or gen is None:
            return None
        ref_loc = face_recognition.face_locations(cv2.cvtColor(ref, cv2.COLOR_BGR2RGB))
        gen_loc = face_recognition.face_locations(cv2.cvtColor(gen, cv2.COLOR_BGR2RGB))
        if not ref_loc or not gen_loc:
            return None
        enc_ref = face_recognition.face_encodings(
            cv2.cvtColor(ref, cv2.COLOR_BGR2RGB), known_face_locations=ref_loc)[0]
        gen_loc.sort(key=lambda b: (b[2] - b[0]) * (b[1] - b[3]), reverse=True)
        enc_gen = face_recognition.face_encodings(
            cv2.cvtColor(gen, cv2.COLOR_BGR2RGB), known_face_locations=gen_loc[:1])[0]
        cos = float(np.dot(enc_ref, enc_gen) /
                    (np.linalg.norm(enc_ref) * np.linalg.norm(enc_gen) + 1e-9))
        return {
            "fidelidade": int(round(max(0.0, min(1.0, cos)) * 100)),
            "metodo": "embeddings",
            "detalhe": "face_recognition cosine similarity",
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Caminho YuNet + SFace (OpenCV 4.10+) — detecção profunda + embeddings
# ---------------------------------------------------------------------------

def _fidelidade_por_yunet(ref_path: str, gen_path: str) -> Optional[Dict]:
    """Detecção YuNet + embeddings SFace (OpenCV 4.10+). Funciona em
    qualquer ângulo, chapéu, iluminação cinematográfica."""
    try:
        import cv2
        # Verificar disponibilidade do YuNet
        if not hasattr(cv2, 'FaceDetectorYN'):
            return None

        img_ref = cv2.imread(ref_path)
        img_gen = cv2.imread(gen_path)
        if img_ref is None or img_gen is None:
            return None

        def _detectar_face_yunet(img):
            h, w = img.shape[:2]
            detector = cv2.FaceDetectorYN.create(
                model="",           # usa modelo embutido do OpenCV
                config="",
                input_size=(w, h),
                score_threshold=0.6,
                nms_threshold=0.3,
                top_k=5000,
            )
            _, faces = detector.detect(img)
            if faces is None or len(faces) == 0:
                return None, None
            # Maior face por área
            best = max(faces, key=lambda f: f[2] * f[3])
            x, y, bw, bh = int(best[0]), int(best[1]), int(best[2]), int(best[3])
            rel_area = (bw * bh) / float(w * h)
            # Filtro de escala calibrado: 0.1% mínimo (era 0.4%)
            if bw < 32 or bh < 32 or rel_area < 0.001:
                return None, "pequena"
            # Margem ao redor da face
            m = int(max(bw, bh) * 0.15)
            x0 = max(0, x - m)
            y0 = max(0, y - m)
            x1 = min(w, x + bw + m)
            y1 = min(h, y + bh + m)
            return img[y0:y1, x0:x1], None

        crop_ref, err_ref = _detectar_face_yunet(img_ref)
        crop_gen, err_gen = _detectar_face_yunet(img_gen)

        if crop_ref is None or crop_gen is None:
            motivo = err_ref or err_gen or "face não detectada"
            return {
                "fidelidade": 0,
                "metodo": "yunet",
                "detalhe": f"YuNet: {motivo}",
                "ok": False,
            }

        # Tentar SFace para cosine similarity
        if hasattr(cv2, 'FaceRecognizerSF'):
            try:
                recognizer = cv2.FaceRecognizerSF.create(model="", config="")
                emb_ref = recognizer.feature(cv2.resize(crop_ref, (112, 112)))
                emb_gen = recognizer.feature(cv2.resize(crop_gen, (112, 112)))
                cosine = float(recognizer.match(
                    emb_ref, emb_gen, cv2.FaceRecognizerSF.FR_COSINE))
                # Normalizar: cosine 0.0–1.0 → score 0–100
                score_100 = int(round(np.clip(cosine * 100, 0, 100)))
                return {
                    "fidelidade": score_100,
                    "metodo": "yunet_sface",
                    "detalhe": f"YuNet + SFace cosine={cosine:.3f}",
                    "ok": True,
                }
            except Exception:
                pass

        # Fallback cromático com crops do YuNet (melhor que Haar)
        rgb_ref = cv2.cvtColor(crop_ref, cv2.COLOR_BGR2RGB)
        rgb_gen = cv2.cvtColor(crop_gen, cv2.COLOR_BGR2RGB)
        mask_ref = _mascara_pele_ycrcb(rgb_ref)
        mask_gen = _mascara_pele_ycrcb(rgb_gen)
        ycc_ref = cv2.cvtColor(crop_ref, cv2.COLOR_BGR2YCrCb)
        ycc_gen = cv2.cvtColor(crop_gen, cv2.COLOR_BGR2YCrCb)
        h_ref = cv2.calcHist([ycc_ref], [1, 2], mask_ref.astype(np.uint8),
                              [32, 32], [0, 256, 0, 256])
        h_gen = cv2.calcHist([ycc_gen], [1, 2], mask_gen.astype(np.uint8),
                              [32, 32], [0, 256, 0, 256])
        cv2.normalize(h_ref, h_ref, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(h_gen, h_gen, 0, 1, cv2.NORM_MINMAX)
        corr = max(0.0, float(cv2.compareHist(h_ref, h_gen, cv2.HISTCMP_CORREL)))
        score_100 = int(round(np.clip(corr * 100, 0, 100)))
        return {
            "fidelidade": score_100,
            "metodo": "yunet_cromatico",
            "detalhe": f"YuNet detecção + histograma YCrCb corr={corr:.3f}",
            "ok": True,
        }
    except Exception as e:
        return None


# ---------------------------------------------------------------------------
# Caminho OpenCV (detecção Haar + correlação) — usado quando face_recognition
# não está disponível mas opencv está (CV real, sem heurística Pillow/YCbCr).
# ---------------------------------------------------------------------------

def _fidelidade_por_opencv(ref_path: str, gen_path: str) -> Optional[Dict]:
    if not _cv2_disponivel():
        return None
    try:
        import cv2
        detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        if detector.empty():
            return None

        def _maior_face(img):
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(48, 48))
            if len(faces) == 0:
                return None
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            m = int(max(w, h) * 0.15)
            x0, y0 = max(0, x - m), max(0, y - m)
            x1 = min(img.shape[1], x + w + m)
            y1 = min(img.shape[0], y + h + m)
            rel_area = (w * h) / float(img.shape[0] * img.shape[1])
            return img[y0:y1, x0:x1], (w, h), rel_area

        img_ref = cv2.imread(ref_path)
        img_gen = cv2.imread(gen_path)
        if img_ref is None or img_gen is None:
            return None

        res_ref = _maior_face(img_ref)
        res_gen = _maior_face(img_gen)
        if res_ref is None or res_gen is None:
            return {
                "fidelidade": 0,
                "metodo": "opencv_haar",
                "detalhe": "nenhuma face detectada (Haar)",
                "ok": False,
            }

        ref_crop_bgr, (rw, rh), _ = res_ref
        gen_crop_bgr, (gw, gh), gen_rel_area = res_gen

        # Filtro de escala calibrado: se a face gerada for muito pequena (< 32px ou < 0.1% da área),
        # trata-se de falso positivo de cenário (folhas, flores, madeira) sem personagem.
        if gw < 32 or gh < 32 or gen_rel_area < 0.001:
            return {
                "fidelidade": 5,
                "metodo": "opencv_haar",
                "detalhe": f"face detectada muito pequena ({gw}x{gh}) — falso positivo de cenário",
                "ok": False,
            }

        rgb_ref = cv2.cvtColor(ref_crop_bgr, cv2.COLOR_BGR2RGB)
        rgb_gen = cv2.cvtColor(gen_crop_bgr, cv2.COLOR_BGR2RGB)
        mask_ref = _mascara_pele_ycrcb(rgb_ref)
        mask_gen = _mascara_pele_ycrcb(rgb_gen)

        frac_gen = float(mask_gen.sum()) / float(mask_gen.size) if mask_gen.size > 0 else 0.0
        if frac_gen < 0.12:
            return {
                "fidelidade": 10,
                "metodo": "opencv_haar",
                "detalhe": f"região detectada sem pele humana suficiente ({frac_gen:.2f})",
                "ok": False,
            }

        # 1. Correlação cromática YCrCb (Cb e Cr nos pixels de pele)
        ycc_ref = cv2.cvtColor(ref_crop_bgr, cv2.COLOR_BGR2YCrCb)
        ycc_gen = cv2.cvtColor(gen_crop_bgr, cv2.COLOR_BGR2YCrCb)
        h_ref_ycc = cv2.calcHist([ycc_ref], [1, 2], mask_ref.astype(np.uint8), [32, 32], [0, 256, 0, 256])
        h_gen_ycc = cv2.calcHist([ycc_gen], [1, 2], mask_gen.astype(np.uint8), [32, 32], [0, 256, 0, 256])
        cv2.normalize(h_ref_ycc, h_ref_ycc, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(h_gen_ycc, h_gen_ycc, 0, 1, cv2.NORM_MINMAX)
        corr_ycc = max(0.0, float(cv2.compareHist(h_ref_ycc, h_gen_ycc, cv2.HISTCMP_CORREL)))

        # 2. Correlação de matiz/saturação HSV
        hsv_ref = cv2.cvtColor(ref_crop_bgr, cv2.COLOR_BGR2HSV)
        hsv_gen = cv2.cvtColor(gen_crop_bgr, cv2.COLOR_BGR2HSV)
        h_ref_hsv = cv2.calcHist([hsv_ref], [0, 1], mask_ref.astype(np.uint8), [30, 32], [0, 180, 0, 256])
        h_gen_hsv = cv2.calcHist([hsv_gen], [0, 1], mask_gen.astype(np.uint8), [30, 32], [0, 180, 0, 256])
        cv2.normalize(h_ref_hsv, h_ref_hsv, 0, 1, cv2.NORM_MINMAX)
        cv2.normalize(h_gen_hsv, h_gen_hsv, 0, 1, cv2.NORM_MINMAX)
        corr_hsv = max(0.0, float(cv2.compareHist(h_ref_hsv, h_gen_hsv, cv2.HISTCMP_CORREL)))

        # 3. Similaridade do tom de pele (distância cromática)
        skin_sim = _similaridade_crops(rgb_ref, rgb_gen) / 100.0

        score = 0.40 * corr_ycc + 0.30 * corr_hsv + 0.30 * skin_sim
        score_100 = int(round(np.clip(score * 100, 0, 100)))

        return {
            "fidelidade": score_100,
            "metodo": "opencv_haar",
            "detalhe": f"Haar + croma YCrCb ({int(corr_ycc*100)}%) + HSV ({int(corr_hsv*100)}%) + pele ({int(skin_sim*100)}%)",
            "ok": True,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# API pública do motor
# ---------------------------------------------------------------------------

def calcular_fidelidade_facial(caminho_referencia: str, caminho_gerada: str) -> Dict:
    """Compara a face da imagem gerada com a referência do personagem.

    Retorna {"fidelidade": int 0-100, "metodo": str, "detalhe": str, "ok": bool}.

    Camadas (na ordem):
      1. embeddings reais de face (face_recognition + opencv);
      2. detecção Haar + correlação (opencv) — sem heurística Pillow/YCbCr;
      3. último recurso OFFLINE (sem opencv/face_recognition): heurística
         determinística Pillow/YCbCr documentada.
    """
    def _log_fidelity(msg: str):
        try:
            try:
                from services.log_service import log_event
            except ImportError:
                from services.event_logger import log_event
            log_event("FIDELITY", msg, level="info")
        except Exception:
            pass
        print(f"[FIDELITY] {msg}", flush=True)

    if not caminho_referencia or not caminho_gerada:
        return {"fidelidade": 0, "metodo": "erro", "detalhe": "caminhos ausentes", "ok": False}
    if not Path(caminho_referencia).exists() or not Path(caminho_gerada).exists():
        return {"fidelidade": 0, "metodo": "erro", "detalhe": "arquivo inexistente", "ok": False}

    # 1. Embeddings reais (se face_recognition + opencv estiverem disponíveis).
    if _face_libs_disponiveis():
        emb = _fidelidade_por_embeddings(caminho_referencia, caminho_gerada)
        if emb is not None:
            emb["ok"] = True
            return emb
        return {
            "fidelidade": 0,
            "metodo": "embeddings",
            "detalhe": "nenhuma face detectada na referência ou na imagem gerada",
            "ok": False,
        }

    # 1b. YuNet + SFace (OpenCV 4.10+ nativo)
    if _cv2_disponivel():
        if not hasattr(__import__('cv2'), 'FaceDetectorYN'):
            _log_fidelity("YuNet indisponível neste build do OpenCV — usando Haar")
        else:
            yunet = _fidelidade_por_yunet(caminho_referencia, caminho_gerada)
            if yunet is not None:
                _log_fidelity(f"YuNet ativo: método={yunet.get('metodo')} fidelidade={yunet.get('fidelidade')}%")
                return yunet
            else:
                _log_fidelity("YuNet retornou None (exceção interna) — fallback para Haar")

    # 2. OpenCV (detecção Haar) — CV real sem a heurística Pillow/YCbCr.
    if _cv2_disponivel():
        cv = _fidelidade_por_opencv(caminho_referencia, caminho_gerada)
        if cv is not None:
            return cv
        return {
            "fidelidade": 0,
            "metodo": "opencv_haar",
            "detalhe": "falha ao processar faces via OpenCV",
            "ok": False,
        }

    # 3. Último recurso (ambiente totalmente offline): heurística Pillow/YCbCr.
    ref_rgb = _carregar_rgb(caminho_referencia)
    gen_rgb = _carregar_rgb(caminho_gerada)
    if ref_rgb is None or gen_rgb is None:
        return {"fidelidade": 0, "metodo": "erro", "detalhe": "imagens ilegíveis", "ok": False}

    crop_ref = _crop_face_referencia(ref_rgb)
    crop_gen, tem_rosto = _crop_face_generated(gen_rgb)
    if not tem_rosto or crop_ref is None or crop_gen is None:
        return {
            "fidelidade": 0,
            "metodo": "heuristica_pele",
            "detalhe": "rosto não detectado na imagem gerada",
            "ok": False,
        }

    score = _similaridade_crops(crop_ref, crop_gen)
    return {
        "fidelidade": int(round(score)),
        "metodo": "heuristica_pele",
        "detalhe": "YCrCb pele + correlação estrutural + histogramas (offline)",
        "ok": True,
    }