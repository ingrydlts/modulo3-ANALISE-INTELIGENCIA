"""
MÓDULO 3 — Análise de Audiência
Roda na segunda segunda-feira do mês via GitHub Actions.

Fluxo:
1. Busca entradas AUDIÊNCIA + NOVO do Notion (comentários, perguntas, dados)
2. Organiza por plataforma (YouTube / Instagram)
3. Envia para Claude que gera análise de público-alvo
4. Salva análise no Notion
5. Marca entradas como PROCESSADO
"""

import os
from datetime import datetime, timezone
from dotenv import load_dotenv
from notion_client import Client as NotionClient
import anthropic

load_dotenv()

# ── Configuração ──────────────────────────────────────────────────────────────
NOTION_TOKEN                = os.environ["NOTION_TOKEN"]
ANTHROPIC_API_KEY           = os.environ["ANTHROPIC_API_KEY"]
NOTION_DB_ID                = os.environ["NOTION_DB_ID"]
NOTION_INTELIGENCIA_PAGE_ID = os.environ["NOTION_INTELIGENCIA_PAGE_ID"]

notion = NotionClient(auth=NOTION_TOKEN)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

MES_ATUAL = datetime.now(timezone.utc).strftime("%B %Y")


# ── Busca de dados ────────────────────────────────────────────────────────────

def buscar_entradas_audiencia() -> list:
    """Busca todas as entradas AUDIÊNCIA + NOVO no banco Notion."""
    resp = notion.databases.query(
        database_id=NOTION_DB_ID,
        filter={
            "and": [
                {"property": "CATEGORIA", "select": {"equals": "AUDIÊNCIA"}},
                {"property": "STATUS",    "select": {"equals": "NOVO"}}
            ]
        }
    )

    entradas = []
    for page in resp.get("results", []):
        nome_prop = page["properties"].get("Name", {}).get("title", [])
        nome = nome_prop[0]["text"]["content"] if nome_prop else "sem título"

        texto_prop = page["properties"].get("Texte", {}).get("rich_text", [])
        texto = "".join(rt.get("text", {}).get("content", "") for rt in texto_prop)

        plataforma_prop = page["properties"].get("PLATAFORMA", {}).get("select")
        plataforma = plataforma_prop["name"] if plataforma_prop else "DESCONHECIDA"

        entradas.append({
            "id":         page["id"],
            "nome":       nome,
            "texto":      texto,
            "plataforma": plataforma
        })

    return entradas


def formatar_entradas(entradas: list) -> str:
    """Organiza entradas por plataforma para o prompt."""
    youtube  = [e for e in entradas if e["plataforma"] == "YOUTUBE"]
    instagram = [e for e in entradas if e["plataforma"] == "INSTAGRAM"]
    outro    = [e for e in entradas if e["plataforma"] not in ("YOUTUBE", "INSTAGRAM")]

    partes = []

    if youtube:
        bloco = "### YouTube\n" + "\n\n".join(
            f"**{e['nome']}**\n{e['texto']}" for e in youtube
        )
        partes.append(bloco)

    if instagram:
        bloco = "### Instagram\n" + "\n\n".join(
            f"**{e['nome']}**\n{e['texto']}" for e in instagram
        )
        partes.append(bloco)

    if outro:
        bloco = "### Outros\n" + "\n\n".join(
            f"**{e['nome']}** [{e['plataforma']}]\n{e['texto']}" for e in outro
        )
        partes.append(bloco)

    return "\n\n---\n\n".join(partes) if partes else "Nenhuma entrada de audiência disponível."


# ── Análise Claude ────────────────────────────────────────────────────────────

def analisar_com_claude(dados_audiencia: str) -> str:
    prompt = f"""Você é o sistema editorial do canal Por Dentro — canal de uma imigrante brasileira na França que explica como a França realmente funciona: trabalho, saúde, burocracia, moradia, cultura.

Posicionamento: observador, lúcido, educativo. O canal é 75% Instagram hoje e tem crescimento forte nessa plataforma.

Analise os dados de audiência abaixo (comentários, perguntas, observações coletadas ao longo do mês) e gere a ANÁLISE DE AUDIÊNCIA de {MES_ATUAL}.

## DADOS DA AUDIÊNCIA
{dados_audiencia}

---

Gere o output na estrutura abaixo. Use as palavras exatas da audiência sempre que possível.

## PERGUNTAS MAIS RECORRENTES
As dúvidas que aparecem mais vezes — essas viram pauta prioritária.

## DORES NÃO ATENDIDAS
O que a audiência precisa que o canal ainda não respondeu bem ou não cobriu.

## PEDIDOS EXPLÍCITOS DE CONTEÚDO
Quando alguém pediu diretamente um tema, formato ou continuação.

## PERFIL DO MOMENTO
Quem está engajando agora: recém-chegado na França, planejando imigrar, já estabelecido? Que fase da vida?

## DIFERENÇA ENTRE PLATAFORMAS
O que a audiência do YouTube quer vs. o que a audiência do Instagram quer. Se houver diferença relevante.

## PAUTAS SUGERIDAS
3 ideias concretas de conteúdo com ângulo específico que saem diretamente desses dados.
Formato: [PLATAFORMA] Título sugerido — por que esse ângulo funciona."""

    resp = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return resp.content[0].text


# ── Salvar no Notion ──────────────────────────────────────────────────────────

def texto_para_blocos(texto: str) -> list:
    """Converte texto com ## headings em blocos Notion."""
    blocos = []
    for linha in texto.split("\n"):
        linha = linha.strip()
        if not linha:
            continue
        if linha.startswith("## "):
            blocos.append({
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"text": {"content": linha[3:]}}]}
            })
        elif linha.startswith("### "):
            blocos.append({
                "object": "block", "type": "heading_3",
                "heading_3": {"rich_text": [{"text": {"content": linha[4:]}}]}
            })
        else:
            blocos.append({
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": linha}}]}
            })
    return blocos


def salvar_analise_no_notion(analise: str):
    titulo = f"ANÁLISE AUDIÊNCIA — {MES_ATUAL}"
    notion.pages.create(
        parent={"page_id": NOTION_INTELIGENCIA_PAGE_ID},
        properties={"title": {"title": [{"text": {"content": titulo}}]}},
        children=texto_para_blocos(analise)
    )
    print(f"  ✓ Salvo: {titulo}")


def marcar_processado(page_id: str):
    notion.pages.update(
        page_id=page_id,
        properties={"STATUS": {"select": {"name": "PROCESSADO"}}}
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n=== Análise de Audiência — {MES_ATUAL} ===\n")

    print("Buscando entradas de audiência...")
    entradas = buscar_entradas_audiencia()

    if not entradas:
        print("Nenhuma entrada de audiência para processar.")
        return

    print(f"{len(entradas)} entrada(s) encontrada(s).")

    dados_formatados = formatar_entradas(entradas)

    print("Enviando para Claude...")
    analise = analisar_com_claude(dados_formatados)

    print("Salvando no Notion...")
    salvar_analise_no_notion(analise)

    print("Marcando entradas como processadas...")
    for e in entradas:
        marcar_processado(e["id"])

    print("\n=== Análise de audiência concluída ===")


if __name__ == "__main__":
    main()
