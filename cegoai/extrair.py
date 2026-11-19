"""Leitura estruturada dos relatórios de Manutenção Preventiva (PDF).

Transforma o PDF numa lista de "unidades" (cada POS, cada SCO e cada área
como Sala IT, Armazém, ...) com os itens do checklist, o estado marcado
(OK / NOK / N/A), as observações e as fotos.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import pymupdf
from PIL import Image

# Marcadores que iniciam uma nova unidade dentro de uma área
INICIO_POS = "CPU (HUB 4POS)"
INICIO_SCO = "SCO CPU"
COLUNAS = ("OK", "NOK", "N/A")


@dataclass
class Foto:
    pagina: int
    xref: int
    secao: str               # equipamento/sub-secção onde aparece
    imagem: Image.Image = field(repr=False)
    categoria: str | None = None      # preenchido pelo classificador
    confianca: float | None = None
    falsa: bool = False               # imagem de preenchimento (ex.: quadrado vermelho)
    probs: dict[str, float] = field(default_factory=dict, repr=False)   # prob. de cada categoria
    arroz: float | None = None        # probabilidade de mostrar o saco de arroz na balança


@dataclass
class Item:
    secao: str
    nome: str
    pagina: int
    estado: str | None = None         # "OK", "NOK", "N/A" ou None (sem resposta)


@dataclass
class Unidade:
    tipo: str                         # "POS", "SCO" ou "AREA"
    nome: str
    numero: str | None = None
    campos: dict[str, str] = field(default_factory=dict)
    itens: list[Item] = field(default_factory=list)
    fotos: list[Foto] = field(default_factory=list)
    observacoes: list[str] = field(default_factory=list)
    rotulos_fotos: int = 0            # quantas vezes aparece o rótulo "Fotos"
    pagina_inicio: int = 0

    @property
    def fotos_reais(self) -> list[Foto]:
        return [f for f in self.fotos if not f.falsa]

    @property
    def paginas(self) -> tuple[int, int]:
        """Primeira e última página ocupadas pela unidade."""
        pags = [self.pagina_inicio] + [i.pagina for i in self.itens] + [f.pagina for f in self.fotos]
        return min(pags), max(pags)


@dataclass
class Relatorio:
    caminho: str
    cabecalho: dict[str, str]
    unidades: list[Unidade]
    paginas_campos: dict[str, int] = field(default_factory=dict)   # campo do cabeçalho -> página

    def por_tipo(self, tipo: str) -> list[Unidade]:
        return [u for u in self.unidades if u.tipo == tipo]


# --------------------------------------------------------------------------
# Leitura de baixo nível: elementos posicionados em cada página
# --------------------------------------------------------------------------
@dataclass
class _El:
    pagina: int
    y: float
    tipo: str          # h1, h2, item, col, valor, texto, check, imagem
    texto: str = ""
    x0: float = 0
    x1: float = 0
    y1: float = 0
    xref: int = 0


def _linhas_texto(page, pno: int) -> list[_El]:
    els: list[_El] = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        for ln in b["lines"]:
            spans = [s for s in ln["spans"] if s["text"].strip()]
            if not spans:
                continue
            txt = "".join(s["text"] for s in spans).strip()
            s0 = spans[0]
            size, bold = s0["size"], "Bold" in s0["font"]
            x0, y0, x1, y1 = ln["bbox"]
            if y0 > page.rect.height - 25:          # número de página
                continue
            if size >= 16 and bold:
                tipo = "h1"
            elif bold and size >= 11.5 and x0 < 60:
                tipo = "h2"
            elif bold and txt in COLUNAS:
                tipo = "col"
            elif bold and x0 < 60:
                tipo = "item"
            elif x0 >= 200:
                tipo = "valor"
            else:
                tipo = "texto"
            els.append(_El(pno, y0, tipo, txt, x0, x1, y1))
    return _juntar_multilinha(els)


def _juntar_multilinha(els: list[_El]) -> list[_El]:
    """Junta rótulos que quebram em várias linhas (ex.: 'Verificar Qualidade' + 'Impressão')."""
    els.sort(key=lambda e: (e.y, e.x0))
    out: list[_El] = []
    for e in els:
        prev = next((p for p in reversed(out) if p.tipo == e.tipo), None)
        if (prev is not None and e.tipo in ("item", "h2") and prev is out[-1]
                and abs(e.x0 - prev.x0) < 3 and e.y - prev.y1 < 6):
            prev.texto += " " + e.texto
            prev.y1 = e.y1
            continue
        out.append(e)
    return out


def _checks(page, pno: int) -> list[_El]:
    """As marcas ✓ são pequenos desenhos vetoriais (linhas + curvas)."""
    out = []
    for d in page.get_drawings():
        tipos = {i[0] for i in d["items"]}
        r = d["rect"]
        if "c" in tipos and r.width < 12 and r.height < 10:
            out.append(_El(pno, r.y0, "check", "", r.x0, r.x1, r.y1))
    return out


def _imagens(page, pno: int) -> list[_El]:
    out = []
    for info in page.get_image_info(xrefs=True):
        x0, y0, x1, y1 = info["bbox"]
        if info["xref"] and (x1 - x0) > 50 and (y1 - y0) > 50:
            out.append(_El(pno, y0, "imagem", "", x0, x1, y1, xref=info["xref"]))
    return out


def _carregar_imagem(doc, xref: int) -> Image.Image:
    dados = doc.extract_image(xref)
    img = Image.open(io.BytesIO(dados["image"]))
    img.load()
    return img.convert("RGB")


# --------------------------------------------------------------------------
# Atribuir cada ✓ a um item e a uma coluna
# --------------------------------------------------------------------------
def _resolver_checks(pagina_els: list[_El], cols_anteriores: list[_El]) -> tuple[dict[int, str], list[_El]]:
    """Devolve ({id(item): estado}, cabeçalho de colunas ativo no fim da página).

    Uma tabela pode continuar na página seguinte sem repetir o cabeçalho
    OK/NOK/N/A; nesse caso usa-se o último cabeçalho da página anterior.
    """
    cols = [e for e in pagina_els if e.tipo == "col"]
    itens = [e for e in pagina_els if e.tipo == "item"]
    estado: dict[int, str] = {}
    for c in (e for e in pagina_els if e.tipo == "check"):
        cy = (c.y + c.y1) / 2
        cx = (c.x0 + c.x1) / 2
        # cabeçalho de colunas mais próximo acima do ✓ (ou o herdado da página anterior)
        acima = [k for k in cols if k.y < c.y]
        if acima:
            y_cab = max(k.y for k in acima)
            grupo = [k for k in acima if abs(k.y - y_cab) < 3]
        elif cols_anteriores:
            y_cab, grupo = -1.0, cols_anteriores
        else:
            continue
        col = min(grupo, key=lambda k: abs((k.x0 + k.x1) / 2 - cx))
        # item cuja faixa vertical está mais próxima do ✓
        cand = [i for i in itens if i.y > y_cab]
        if not cand:
            continue
        it = min(cand, key=lambda i: abs((i.y + i.y1) / 2 - cy))
        if abs((it.y + it.y1) / 2 - cy) < 16:
            estado[id(it)] = col.texto
    if cols:
        y_ult = max(k.y for k in cols)
        cols_anteriores = [k for k in cols if abs(k.y - y_ult) < 3]
    return estado, cols_anteriores


# --------------------------------------------------------------------------
# Construção das unidades
# --------------------------------------------------------------------------
def ler_relatorio(caminho: str) -> Relatorio:
    doc = pymupdf.open(caminho)
    elementos: list[_El] = []
    estados: dict[int, str] = {}
    cols_ativas: list[_El] = []
    for pno, page in enumerate(doc):
        els = _linhas_texto(page, pno) + _checks(page, pno) + _imagens(page, pno)
        novos, cols_ativas = _resolver_checks(els, cols_ativas)
        estados.update(novos)
        els.sort(key=lambda e: (e.y, e.x0))
        elementos.extend(els)

    cabecalho: dict[str, str] = {}
    paginas_campos: dict[str, int] = {}
    unidades: list[Unidade] = []
    area = None                  # título h1 atual
    atual: Unidade | None = None
    secao = ""                   # h2 atual
    ultimo_h2: _El | None = None
    num_pos_pendente: str | None = None
    em_observacoes = False

    def nova(tipo, nome, pno, numero=None):
        nonlocal atual
        atual = Unidade(tipo=tipo, nome=nome, numero=numero, pagina_inicio=pno + 1)
        unidades.append(atual)

    for e in elementos:
        if e.tipo == "h1":
            em_observacoes = False
            if e.texto == "Manutenção Preventiva":
                continue
            area = e.texto
            secao = ""
            if area in ("POS", "SCOs"):
                atual = None     # as unidades começam no marcador CPU
            elif area == "Dados da Loja":
                atual = None
            else:
                nova("AREA", area, e.pagina)
            continue

        if e.tipo == "h2":
            em_observacoes = e.texto.startswith("Observações")
            ultimo_h2 = e
            if area == "POS" and e.texto == INICIO_POS:
                n = sum(1 for u in unidades if u.tipo == "POS") + 1
                nova("POS", f"POS #{n}", e.pagina, num_pos_pendente)
                num_pos_pendente = None
            elif area == "SCOs" and e.texto == INICIO_SCO:
                n = sum(1 for u in unidades if u.tipo == "SCO") + 1
                nova("SCO", f"SCO #{n}", e.pagina)
            if e.texto.startswith("Fotos"):
                if atual is not None:
                    atual.rotulos_fotos += 1
            elif not em_observacoes and e.texto != "Número do POS":
                secao = e.texto
            continue

        if e.tipo == "valor":
            if ultimo_h2 is not None and abs(e.y - ultimo_h2.y) < 14 and e.pagina == ultimo_h2.pagina:
                chave = ultimo_h2.texto
                if chave == "Número do POS":
                    num_pos_pendente = e.texto
                elif atual is None:
                    cabecalho[chave] = e.texto
                    paginas_campos[chave] = e.pagina + 1
                else:
                    atual.campos[chave] = e.texto
            elif e.pagina == 0 and e.y < 80:
                cabecalho["Data"] = e.texto
            continue

        if e.tipo == "texto" and em_observacoes and atual is not None:
            atual.observacoes.append(e.texto)
            continue

        if atual is None:
            continue
        if e.tipo == "item":
            atual.itens.append(Item(secao, e.texto, e.pagina + 1, estados.get(id(e))))
        elif e.tipo == "imagem":
            img = _carregar_imagem(doc, e.xref)
            atual.fotos.append(Foto(e.pagina + 1, e.xref, secao, img))

    # campos da área "Fecho da Intervenção" e da área POS/SCO vão para o cabeçalho
    for u in unidades:
        if u.nome == "Fecho da Intervenção":
            cabecalho.update(u.campos)
    return Relatorio(caminho, cabecalho, unidades, paginas_campos)
