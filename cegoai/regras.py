"""Aplica as regras de completude a um relatório já lido e com fotos analisadas."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .extrair import Relatorio, Unidade


@dataclass
class Problema:
    nivel: str          # "FALTA" (reprova) ou "AVISO"
    onde: str
    mensagem: str
    paginas: str = ""   # páginas do PDF onde está o problema, ex.: "12, 15-17"


@dataclass
class Resultado:
    relatorio: Relatorio
    problemas: list[Problema] = field(default_factory=list)

    @property
    def veredito(self) -> str:
        return "FALTA" if any(p.nivel == "FALTA" for p in self.problemas) else "OK"


def carregar_regras(caminho: str) -> dict:
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def _numero(txt: str | None) -> int | None:
    m = re.search(r"\d+", txt or "")
    return int(m.group()) if m else None


def _paginas(pags) -> str:
    """[3, 4, 5, 9] -> "3-5, 9"."""
    pags = sorted(set(p for p in pags if p))
    partes, i = [], 0
    while i < len(pags):
        j = i
        while j + 1 < len(pags) and pags[j + 1] == pags[j] + 1:
            j += 1
        partes.append(str(pags[i]) if i == j else f"{pags[i]}-{pags[j]}")
        i = j + 1
    return ", ".join(partes)


def _intervalo(u: Unidade) -> str:
    a, b = u.paginas
    return _paginas(range(a, b + 1)) if b > a else str(a)


def _rotulo(u: Unidade) -> str:
    if u.tipo == "POS" and u.numero:
        return f"{u.nome} (n.º {u.numero})"
    return u.nome


def _categorias_presentes(u: Unidade, prob_min: float) -> set[str]:
    """Categorias vistas nas fotos: a escolhida e qualquer outra com probabilidade >= prob_min
    (ex.: um talão fotografado por cima da balança conta como talão e como balança)."""
    out = {f.categoria for f in u.fotos_reais if f.categoria}
    for f in u.fotos_reais:
        out |= {c for c, p in f.probs.items() if p >= prob_min}
    return out


def validar(rel: Relatorio, regras: dict) -> Resultado:
    res = Resultado(rel)
    add = lambda nivel, onde, msg, pags="": res.problemas.append(Problema(nivel, onde, msg, pags))
    g = regras["geral"]
    nivel_nok = "FALTA" if g.get("nok_reprova", True) else "AVISO"
    nivel_vazio = "FALTA" if g.get("sem_resposta_reprova", True) else "AVISO"

    # 1. Contagem de POS / SCO declarada vs. secções preenchidas
    if g.get("verificar_contagem_pos_sco", True):
        decl_pos = _numero(rel.cabecalho.get("Quantos POS tem a loja?"))
        n_pos = len(rel.por_tipo("POS"))
        if decl_pos is not None and decl_pos != n_pos:
            add("FALTA", "POS", f"a loja declara {decl_pos} POS mas o relatório tem {n_pos}",
                _paginas([rel.paginas_campos.get("Quantos POS tem a loja?")]))
        decl_sco = _numero(rel.cabecalho.get("Quantos SCOs tem a loja?"))
        n_sco = len(rel.por_tipo("SCO"))
        if decl_sco is not None and decl_sco != n_sco:
            add("FALTA", "SCO", f"a loja declara {decl_sco} SCO mas o relatório tem {n_sco}",
                _paginas([rel.paginas_campos.get("Quantos SCOs tem a loja?")]))

    for u in rel.unidades:
        onde = _rotulo(u)

        # 2. Fotos falsas (imagem de preenchimento)
        falsas = [f for f in u.fotos if f.falsa]
        if falsas:
            add("FALTA", onde, f"{len(falsas)} foto(s) falsa(s)/de preenchimento",
                _paginas(f.pagina for f in falsas))

        # 3. Checklist: NOK e itens sem resposta
        noks = [i for i in u.itens if i.estado == "NOK"]
        vazios = [i for i in u.itens if i.estado is None]
        if noks:
            nomes = "; ".join(f"{i.secao} › {i.nome} (pág. {i.pagina})" for i in noks[:4])
            extra = f" (+{len(noks) - 4})" if len(noks) > 4 else ""
            add(nivel_nok, onde, f"{len(noks)} item(ns) NOK: {nomes}{extra}", _paginas(i.pagina for i in noks))
            if not u.observacoes:
                add("AVISO", onde, "há itens NOK sem observação a justificar", _paginas(i.pagina for i in noks))
        if vazios:
            nomes = "; ".join(f"{i.secao} › {i.nome} (pág. {i.pagina})" for i in vazios[:4])
            extra = f" (+{len(vazios) - 4})" if len(vazios) > 4 else ""
            add(nivel_vazio, onde, f"{len(vazios)} item(ns) sem resposta: {nomes}{extra}",
                _paginas(i.pagina for i in vazios))

        # 4. Fotos obrigatórias por tipo de unidade
        r = regras.get(u.tipo, {})
        if u.tipo in ("POS", "SCO"):
            reais = len(u.fotos_reais)
            if reais < r.get("min_fotos_reais", 1):
                add("FALTA", onde, f"só {reais} foto(s) real(is) (mínimo {r.get('min_fotos_reais', 1)})",
                    _intervalo(u))
            elif any(f.categoria for f in u.fotos_reais):     # só se as fotos foram classificadas
                presentes = _categorias_presentes(u, g.get("prob_presenca", 0.2))
                exig = list(r.get("fotos_esperadas", []))
                for c in r.get("fotos_condicionais", []):
                    if u.campos.get(c["campo"]) == c["valor"]:
                        exig.append(c["categoria"])
                em_falta = [e for e in exig if not set(e.split("|")) & presentes]
                if em_falta:
                    add(r.get("nivel_fotos_esperadas", "AVISO"), onde,
                        "não encontrei foto de: " + ", ".join(e.replace("|", "/") for e in em_falta),
                        _intervalo(u))
            # 5. Teste de peso: saco de 1 kg de arroz em cima da balança
            if r.get("arroz_na_balanca") and reais and all(f.arroz is not None for f in u.fotos_reais):
                melhor = max(f.arroz for f in u.fotos_reais)
                if melhor < g.get("limiar_arroz", 0.5):
                    add(r.get("nivel_arroz", "FALTA"), onde,
                        "não encontrei foto da balança com o saco de 1 kg de arroz", _intervalo(u))
        elif u.rotulos_fotos:
            minimo = r.get("min_fotos_por_rotulo", 1) * u.rotulos_fotos
            if len(u.fotos_reais) < minimo:
                add("FALTA", onde, f"{len(u.fotos_reais)} foto(s) para {u.rotulos_fotos} campo(s) 'Fotos'",
                    _intervalo(u))
    return res
