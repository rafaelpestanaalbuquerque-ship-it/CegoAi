# CegoAi

Verifica automaticamente se um relatório de **Manutenção Preventiva** (PDF) está completo.

## Instalação

Requer Python 3.13 (o 3.15 ainda não tem todas as bibliotecas de IA).

```
py install 3.13
py -V:3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Utilização

**Forma mais simples:** coloque os PDFs em `iniciar\pdfs\` e faça duplo-clique em [iniciar/CegoAi.bat](iniciar/CegoAi.bat). Também pode arrastar PDFs ou pastas para cima do `CegoAi.bat`. O resultado detalhado fica em `iniciar\resultado.json`.

Pela linha de comandos:

```
.venv\Scripts\python cegoai.py relatorio.pdf
.venv\Scripts\python cegoai.py dados\Teste                 # pasta inteira
.venv\Scripts\python cegoai.py dados --avaliar             # compara com as pastas Ok\ e Falta\
.venv\Scripts\python cegoai.py dados\Teste --json out.json
.venv\Scripts\python cegoai.py dados --exportar-fotos dataset   # junta fotos por categoria
.venv\Scripts\python cegoai.py relatorio.pdf --sem-ia      # só regras, sem classificar fotos
```

## O que verifica

| Regra | Nível |
|---|---|
| Fotos falsas / de preenchimento (ex.: quadrado vermelho ou preto) | FALTA |
| POS/SCO com menos de 2 fotos reais | FALTA |
| Itens do checklist sem resposta | FALTA |
| Nº de POS/SCO declarado ≠ nº de secções preenchidas | FALTA |
| POS/SCO sem foto da balança com o saco de 1 kg de arroz | FALTA |
| Área com campo "Fotos" sem foto | FALTA |
| Tipo de foto esperado não encontrado (TPA, talão, balança, cablagem, ecrã BOBA) | FALTA |
| Itens do checklist marcados NOK | AVISO |
| NOK sem observação | AVISO |

Cada problema indica a(s) página(s) do PDF onde está (`→ pág. 30`).

As regras estão em [regras.json](regras.json) e podem ser editadas sem mexer no código.
Para o arroz passar a ser só aviso, mude `"nivel_arroz"` para `"AVISO"`; o limite de deteção é `"limiar_arroz"` (0.5).

## Como funciona

1. [cegoai/extrair.py](cegoai/extrair.py) lê o PDF e separa-o em unidades (cada POS, SCO e área), com os itens, as marcas ✓ (OK/NOK/N/A), as observações e as fotos.
2. [cegoai/fotos.py](cegoai/fotos.py) deteta fotos falsas e classifica o conteúdo das fotos com o CLIP (modelo de visão pré-treinado, sem treino próprio).
3. [cegoai/regras.py](cegoai/regras.py) aplica as regras e dá o veredito OK / FALTA.

## Próximo passo: modelo próprio

O classificador de fotos é *zero-shot* e ainda confunde algumas fotos. Com `--exportar-fotos` as fotos ficam separadas em pastas por categoria. Depois de corrigir as pastas à mão (algumas centenas de fotos), dá para treinar um classificador próprio no CPU.
