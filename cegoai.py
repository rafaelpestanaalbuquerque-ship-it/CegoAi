"""CegoAi — verifica se um relatório de Manutenção Preventiva (PDF) está completo.

Exemplos:
    python cegoai.py relatorio.pdf
    python cegoai.py dados/Teste                    # todos os PDFs da pasta
    python cegoai.py dados --avaliar                # compara com as pastas Ok/ e Falta/
    python cegoai.py dados/Teste --json resultado.json
    python cegoai.py dados/Ok --exportar-fotos dataset   # guarda fotos por categoria (para treino)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")

from cegoai.extrair import ler_relatorio          # noqa: E402
from cegoai.fotos import eh_falsa                 # noqa: E402
from cegoai.regras import carregar_regras, validar  # noqa: E402

RAIZ = Path(__file__).resolve().parent


def listar_pdfs(caminhos: list[str]) -> list[Path]:
    out: list[Path] = []
    for c in map(Path, caminhos):
        out += sorted(c.rglob("*.pdf")) if c.is_dir() else [c]
    return out


def rotulo_esperado(pdf: Path) -> str | None:
    """Pasta 'Ok' ou 'Falta' no caminho indica a classificação esperada."""
    for parte in reversed(pdf.parts[:-1]):
        if parte.lower() == "ok":
            return "OK"
        if parte.lower() == "falta":
            return "FALTA"
    return None


def analisar(pdf: Path, regras: dict, clf, exportar: Path | None):
    rel = ler_relatorio(str(pdf))
    unidades_foto = [u for u in rel.unidades if u.tipo in ("POS", "SCO")]
    for u in rel.unidades:
        for f in u.fotos:
            f.falsa = eh_falsa(f.imagem)
    if clf is not None:
        fotos = [f for u in unidades_foto for f in u.fotos_reais]
        for f, (cat, conf, arroz, probs) in zip(fotos, clf.classificar([f.imagem for f in fotos])):
            f.categoria, f.confianca, f.arroz, f.probs = cat, conf, arroz, probs
    if exportar is not None:
        for u in rel.unidades:
            for f in u.fotos:
                cat = "falsa" if f.falsa else (f.categoria or u.tipo.lower())
                d = exportar / cat
                d.mkdir(parents=True, exist_ok=True)
                f.imagem.save(d / f"{pdf.stem}_p{f.pagina}_{f.xref}.jpg", quality=90)
    return validar(rel, regras)


def imprimir(res, esperado: str | None):
    rel = res.relatorio
    c = rel.cabecalho
    marca = "✅" if res.veredito == "OK" else "❌"
    print(f"\n{marca} {res.veredito}  {Path(rel.caminho).name}")
    print(f"   Loja {c.get('Número de Loja', '?')} {c.get('Loja', '')} · {c.get('Data', '')} · "
          f"Técnico: {c.get('Técnico', '?')} · {len(rel.por_tipo('POS'))} POS, {len(rel.por_tipo('SCO'))} SCO")
    if esperado:
        print(f"   Esperado: {esperado} → {'acertou' if esperado == res.veredito else 'ERROU'}")
    for nivel in ("FALTA", "AVISO"):
        for p in (p for p in res.problemas if p.nivel == nivel):
            pag = f"  → pág. {p.paginas}" if p.paginas else ""
            print(f"   [{p.nivel}] {p.onde}: {p.mensagem}{pag}")


def como_dict(res) -> dict:
    rel = res.relatorio
    return {
        "ficheiro": rel.caminho,
        "veredito": res.veredito,
        "cabecalho": rel.cabecalho,
        "problemas": [p.__dict__ for p in res.problemas],
        "unidades": [
            {"tipo": u.tipo, "nome": u.nome, "numero": u.numero,
             "fotos": [{"pagina": f.pagina, "categoria": f.categoria, "confianca": f.confianca,
                        "falsa": f.falsa, "arroz": f.arroz} for f in u.fotos],
             "itens_nok": [f"{i.secao} › {i.nome}" for i in u.itens if i.estado == "NOK"]}
            for u in rel.unidades
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("caminhos", nargs="+", help="PDF(s) ou pasta(s)")
    ap.add_argument("--regras", default=str(RAIZ / "regras.json"))
    ap.add_argument("--sem-ia", action="store_true", help="não classificar o conteúdo das fotos (mais rápido)")
    ap.add_argument("--avaliar", action="store_true", help="mostrar acerto face às pastas Ok/ e Falta/")
    ap.add_argument("--json", help="guardar resultado em JSON")
    ap.add_argument("--exportar-fotos", help="pasta onde guardar as fotos separadas por categoria")
    args = ap.parse_args()

    regras = carregar_regras(args.regras)
    pdfs = listar_pdfs(args.caminhos)
    if not pdfs:
        sys.exit("Nenhum PDF encontrado.")

    clf = None
    if not args.sem_ia:
        print("A carregar o modelo de visão (CLIP)...", flush=True)
        from cegoai.fotos import Classificador
        clf = Classificador()

    exportar = Path(args.exportar_fotos) if args.exportar_fotos else None
    resultados, acertos, avaliados = [], 0, 0
    for pdf in pdfs:
        t = time.time()
        try:
            res = analisar(pdf, regras, clf, exportar)
        except Exception as e:  # um PDF estragado não deve parar o lote
            print(f"\n⚠️  ERRO a ler {pdf.name}: {e}")
            continue
        esperado = rotulo_esperado(pdf) if args.avaliar else None
        imprimir(res, esperado)
        print(f"   ({time.time() - t:.0f}s)")
        resultados.append(res)
        if esperado:
            avaliados += 1
            acertos += esperado == res.veredito

    n_ok = sum(r.veredito == "OK" for r in resultados)
    print(f"\nResumo: {len(resultados)} PDF(s) · {n_ok} OK · {len(resultados) - n_ok} FALTA")
    if avaliados:
        print(f"Acerto face às pastas Ok/Falta: {acertos}/{avaliados}")
    if args.json:
        Path(args.json).write_text(json.dumps([como_dict(r) for r in resultados], ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"JSON guardado em {args.json}")


if __name__ == "__main__":
    main()
