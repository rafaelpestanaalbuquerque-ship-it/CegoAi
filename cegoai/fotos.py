"""Análise das fotos: deteção de imagens falsas e classificação do conteúdo.

A classificação usa o CLIP (modelo de visão pré-treinado) em modo "zero-shot":
cada categoria é descrita por frases em inglês e a foto recebe a categoria
cuja descrição é mais parecida. Não exige treino; quando houver fotos
suficientes rotuladas, este módulo pode ser substituído por um classificador
ajustado (ver `--exportar-fotos`).
"""
from __future__ import annotations

import hashlib
from collections import Counter

import numpy as np
from PIL import Image

# Descrições de cada categoria (várias frases por categoria melhoram o resultado)
CATEGORIAS: dict[str, list[str]] = {
    "tpa": [
        "a photo of a card payment terminal with a keypad",
        "a Verifone payment terminal screen showing a QR code and a contactless symbol",
    ],
    "talao": [
        "a photo of a printed paper receipt coming out of a receipt printer",
        "a close-up of a supermarket paper receipt with prices",
        "a black thermal printer with a white paper receipt sticking out of it",
        "a paper receipt with the words Abandono Talao printed on it",
    ],
    "balanca": [
        "a supermarket checkout scale with a bag of rice on it and a touchscreen showing the weight",
        "a bag of rice on a glass barcode scanner scale at a checkout",
    ],
    "scanner": [
        "top view of a barcode scanner scale with red lights at a self checkout",
        "an open barcode scanner scale seen from above",
    ],
    "boba": [
        "a monitor showing a camera image of the bottom of a shopping cart with a red border",
        "a screen displaying a security camera view of a shopping trolley",
    ],
    "cablagem": [
        "close-up of cables plugged into the ports of a black computer hub",
        "the back of a computer with usb and network cables connected",
        "an open metal compartment under a checkout counter with a round hole and cables",
    ],
    "base_balanca": [
        "a blue metal tray with four round load cells and black cables",
        "an empty blue metal compartment under a checkout",
    ],
    "outro": [
        "a computer monitor with a keyboard and mouse on a desk",
        "a laser printer in an office",
        "a server rack with network cables",
        "an empty room with a grey floor",
        "a fingerprint time clock on a wall",
        "a mobile phone",
        "a wall mounted TV screen",
        "a cash counting machine",
        "a portable label printer",
        "headset chargers on a wall",
        "a solid red rectangle",
    ],
}

# Teste de peso: a balança deve ter o saco de 1 kg de arroz em cima
ARROZ_SIM = [
    "a 1 kg bag of rice lying on a supermarket checkout scale",
    "a package of rice on the glass plate of a barcode scanner scale",
    "a red package of rice on a checkout scale",
    "a white and purple bag of rice on a self checkout scale",
    "a bag of rice on a checkout scale under a touchscreen monitor",
    "a plastic package of food lying on a checkout counter scale",
    "a bag of rice",
]
ARROZ_NAO = [
    "an empty supermarket checkout scale with nothing on it",
    "an empty glass barcode scanner scale",
    "a touchscreen showing a weighing screen with zero weight",
    "a yellow weighing window on a checkout touchscreen",
    "a list of registered items on a checkout touchscreen",
    "a printed paper receipt",
    "a card payment terminal",
    "cables connected to a computer",
    "an open barcode scanner seen from above",
    "a security camera view of a shopping cart on a monitor",
    "a blue metal tray with load cells",
    "a black receipt printer with a white paper receipt",
    "a paper receipt with printed text",
    "an empty metal compartment with a round hole",
    "an empty checkout counter",
    "a ceiling with lights and a sign",
    "a touchscreen monitor",
]


def eh_falsa(img: Image.Image, limite_desvio: float = 13.0) -> bool:
    """Imagem de preenchimento: quase sem variação de cor e sem detalhe (ex.: quadrado vermelho).

    Fotos reais escuras têm pouca variação de cor mas mantêm detalhe (arestas),
    por isso também se mede a energia de arestas.
    """
    a = np.asarray(img.convert("RGB").resize((96, 96)), dtype=np.float32)
    desvio = a.std(axis=(0, 1)).mean()
    cinza = a.mean(axis=2)
    arestas = np.abs(np.diff(cinza, axis=0)).mean() + np.abs(np.diff(cinza, axis=1)).mean()
    return bool(desvio < limite_desvio and arestas < 3.0)


def impressao_digital(img: Image.Image) -> str:
    """Hash perceptual simples (8x8) para detetar a mesma foto repetida."""
    a = np.asarray(img.convert("L").resize((8, 8)), dtype=np.float32)
    bits = (a > a.mean()).flatten()
    return hashlib.md5(np.packbits(bits).tobytes()).hexdigest()[:12]


def repetidas(imagens: list[Image.Image]) -> set[int]:
    """Índices de imagens cuja impressão digital aparece mais de uma vez."""
    digs = [impressao_digital(i) for i in imagens]
    cont = Counter(digs)
    return {k for k, d in enumerate(digs) if cont[d] > 1}


class Classificador:
    """Classificador zero-shot baseado no CLIP (corre no CPU)."""

    def __init__(self, modelo: str = "ViT-B-32", pesos: str = "laion2b_s34b_b79k"):
        import open_clip
        import torch

        self.torch = torch
        torch.set_grad_enabled(False)
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(modelo, pretrained=pesos)
        self.model.eval()
        tok = open_clip.get_tokenizer(modelo)
        self.rotulos: list[str] = []
        frases: list[str] = []
        for cat, descr in CATEGORIAS.items():
            for d in descr:
                self.rotulos.append(cat)
                frases.append(d)
        t = self.model.encode_text(tok(frases))
        self.texto = t / t.norm(dim=-1, keepdim=True)
        a = self.model.encode_text(tok(ARROZ_SIM + ARROZ_NAO))
        self.texto_arroz = a / a.norm(dim=-1, keepdim=True)

    def _arroz(self, imagens: list[Image.Image]) -> list[float]:
        """Probabilidade de cada imagem mostrar o saco de arroz na balança.

        Usa só a parte de baixo da foto (60%), onde fica o prato da balança:
        nas fotos de corpo inteiro o ecrã ocupa quase tudo e o saco passa despercebido.
        """
        baixo = [im.convert("RGB").crop((0, int(im.height * 0.4), im.width, im.height)) for im in imagens]
        f = self.model.encode_image(self.torch.stack([self.preprocess(im) for im in baixo]))
        f = f / f.norm(dim=-1, keepdim=True)
        sims = 100.0 * f @ self.texto_arroz.T
        n = len(ARROZ_SIM)
        duas = self.torch.stack([sims[:, :n].max(dim=1).values, sims[:, n:].max(dim=1).values], dim=1)
        return duas.softmax(dim=1)[:, 0].tolist()

    def classificar(self, imagens: list[Image.Image], lote: int = 16) -> list[tuple[str, float, float, dict]]:
        """Devolve (categoria, confiança, prob. de arroz na balança, prob. de cada categoria) por imagem."""
        torch = self.torch
        out: list[tuple[str, float, float, dict]] = []
        for i in range(0, len(imagens), lote):
            x = torch.stack([self.preprocess(im) for im in imagens[i:i + lote]])
            f = self.model.encode_image(x)
            f = f / f.norm(dim=-1, keepdim=True)
            sims = (100.0 * f @ self.texto.T)
            # melhor frase de cada categoria -> softmax entre categorias
            cats = list(CATEGORIAS)
            por_cat = torch.stack(
                [sims[:, [k for k, r in enumerate(self.rotulos) if r == c]].max(dim=1).values for c in cats],
                dim=1,
            )
            prob = por_cat.softmax(dim=1)
            conf, idx = prob.max(dim=1)
            arroz = self._arroz(imagens[i:i + lote])
            out += [(cats[j], float(c), a, dict(zip(cats, p)))
                    for j, c, a, p in zip(idx.tolist(), conf.tolist(), arroz, prob.tolist())]
        return out
