"""
MÓDULO 3 — Coleta Semanal YouTube
Roda toda sexta à noite via GitHub Actions.

Fluxo:
1. Lê entradas CONCORRÊNCIA + YOUTUBE + NOVO do Notion (FICHIERS INSTAGRAM)
2. Para cada entrada, busca dados do canal no YouTube Data API
3. Salva os dados coletados como nova página no Notion (Inteligência)
4. Marca a entrada original como PROCESSADO
"""

import os
import re
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
from notion_client import Client as NotionClient
from notion_client.errors import APIResponseError
from googleapiclient.discovery import build

load_dotenv()

# ── Configuração ──────────────────────────────────────────────────────────────
NOTION_TOKEN                = os.environ.get("NOTION_TOKEN")
YOUTUBE_API_KEY             = os.environ.get("YOUTUBE_API_KEY")
NOTION_DB_ID                = os.environ.get("NOTION_DB_ID")
NOTION_INTELIGENCIA_PAGE_ID = os.environ.get("NOTION_INTELIGENCIA_PAGE_ID")
NOTION_COLETAS_DB_ID        = os.environ.get("NOTION_COLETAS_DB_ID")

if not all([NOTION_TOKEN, YOUTUBE_API_KEY, NOTION_DB_ID, NOTION_INTELIGENCIA_PAGE_ID, NOTION_COLETAS_DB_ID]):
    raise SystemExit("❌ Variável de ambiente ausente. Verifique: NOTION_TOKEN, YOUTUBE_API_KEY, NOTION_DB_ID, NOTION_INTELIGENCIA_PAGE_ID, NOTION_COLETAS_DB_ID")

notion  = NotionClient(auth=NOTION_TOKEN)
youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)


# ── Validação do alvo Notion ──────────────────────────────────────────────────

def detectar_tipo_alvo(target_id: str) -> str:
    """Detecta se o ID é uma page ou database no Notion."""
    try:
        notion.pages.retrieve(target_id)
        return "page"
    except APIResponseError:
        pass
    try:
        notion.databases.retrieve(target_id)
        return "database"
    except APIResponseError:
        return None

TARGET_TYPE = detectar_tipo_alvo(NOTION_INTELIGENCIA_PAGE_ID)
if not TARGET_TYPE:
    raise SystemExit(
        f"❌ Não foi possível acessar NOTION_INTELIGENCIA_PAGE_ID ({NOTION_INTELIGENCIA_PAGE_ID}).\n"
        "Verifique se a página existe e foi compartilhada com a integração Notion."
    )

MESES_PT = {
    "January": "Janeiro", "February": "Fevereiro", "March": "Março",
    "April": "Abril", "May": "Maio", "June": "Junho",
    "July": "Julho", "August": "Agosto", "September": "Setembro",
    "October": "Outubro", "November": "Novembro", "December": "Dezembro"
}

_now          = datetime.now(timezone.utc)
MES_ATUAL     = _now.strftime("%B %Y")
MES_ATUAL_PT  = MESES_PT[_now.strftime("%B")]


# ── Helpers YouTube ───────────────────────────────────────────────────────────

def extrair_channel_id(url: str) -> str | None:
    """Extrai o channel ID de URLs do YouTube (formatos /channel/, @handle e /c/ legado)."""
    # Formato: youtube.com/channel/UCxxxxxx
    match = re.search(r'youtube\.com/channel/(UC[\w-]+)', url)
    if match:
        return match.group(1)

    # Formato: youtube.com/@handle
    match = re.search(r'youtube\.com/@([\w.-]+)', url)
    if match:
        handle = match.group(1)
        resp = youtube.channels().list(part="id", forHandle=handle).execute()
        items = resp.get("items", [])
        return items[0]["id"] if items else None

    # Formato legado: youtube.com/c/NomeDoCanal ou youtube.com/user/NomeDoCanal
    match = re.search(r'youtube\.com/(?:c|user)/([\w.-]+)', url)
    if match:
        nome = match.group(1)
        # Tenta buscar pelo nome do canal via search
        resp = youtube.search().list(
            part="snippet",
            q=nome,
            type="channel",
            maxResults=1
        ).execute()
        items = resp.get("items", [])
        return items[0]["snippet"]["channelId"] if items else None

    return None


def buscar_dados_canal(channel_id: str) -> dict | None:
    """Busca informações, thumbnail e últimos 10 vídeos do canal."""
    canal_resp = youtube.channels().list(
        part="snippet,contentDetails,statistics",
        id=channel_id
    ).execute()

    items = canal_resp.get("items", [])
    if not items:
        return None

    canal      = items[0]
    uploads_id = canal["contentDetails"]["relatedPlaylists"]["uploads"]
    stats      = canal.get("statistics", {})
    thumbnails = canal["snippet"].get("thumbnails", {})

    # Thumbnail do canal (melhor resolução disponível)
    thumbnail_url = (
        thumbnails.get("high", {}).get("url") or
        thumbnails.get("medium", {}).get("url") or
        thumbnails.get("default", {}).get("url") or ""
    )

    # Últimos 10 vídeos
    playlist_resp = youtube.playlistItems().list(
        part="contentDetails",
        playlistId=uploads_id,
        maxResults=10
    ).execute()

    video_ids = [i["contentDetails"]["videoId"] for i in playlist_resp.get("items", [])]

    videos = []
    if video_ids:
        videos_resp = youtube.videos().list(
            part="snippet,statistics",
            id=",".join(video_ids)
        ).execute()

        for v in videos_resp.get("items", []):
            videos.append({
                "titulo":      v["snippet"]["title"],
                "publicado":   v["snippet"]["publishedAt"][:10],
                "views":       int(v["statistics"].get("viewCount", 0)),
                "likes":       int(v["statistics"].get("likeCount", 0)),
                "comentarios": int(v["statistics"].get("commentCount", 0)),
                "url":         f"https://youtube.com/watch?v={v['id']}",
                "thumbnail":   v["snippet"].get("thumbnails", {}).get("high", {}).get("url", "")
            })

    # Vídeo com mais views (para usar como capa se preferir)
    top_video = max(videos, key=lambda v: v["views"]) if videos else None

    return {
        "nome":          canal["snippet"]["title"],
        "inscritos":     int(stats.get("subscriberCount", 0)),
        "total_videos":  int(stats.get("videoCount", 0)),
        "thumbnail_url": thumbnail_url,
        "top_video":     top_video,
        "videos":        videos
    }


# ── Helpers Notion ────────────────────────────────────────────────────────────

def buscar_entradas_novas() -> list:
    """Retorna entradas CONCORRÊNCIA + YOUTUBE + NOVO do banco Notion."""
    resp = notion.databases.query(
        database_id=NOTION_DB_ID,
        filter={
            "and": [
                {"property": "CATEGORIA",  "select": {"equals": "CONCORRÊNCIA"}},
                {"property": "PLATAFORMA", "select": {"equals": "YOUTUBE"}},
                {"property": "STATUS",     "select": {"equals": "NOVO"}}
            ]
        }
    )
    return resp.get("results", [])


def marcar_processado(page_id: str):
    notion.pages.update(
        page_id=page_id,
        properties={"STATUS": {"select": {"name": "PROCESSADO"}}}
    )


def montar_blocos_videos(videos: list) -> list:
    """Monta blocos visuais para os vídeos recentes."""
    blocos = [
        {
            "object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"text": {"content": "Últimos vídeos"}}]}
        },
        {
            "object": "block", "type": "divider",
            "divider": {}
        }
    ]

    for v in videos:
        # Thumbnail do vídeo como imagem inline
        if v.get("thumbnail"):
            blocos.append({
                "object": "block", "type": "image",
                "image": {"type": "external", "external": {"url": v["thumbnail"]}}
            })

        blocos.append({
            "object": "block", "type": "callout",
            "callout": {
                "rich_text": [{"text": {"content":
                    f"{v['titulo']}\n"
                    f"📅 {v['publicado']}  •  👁 {v['views']:,} views  •  👍 {v['likes']:,}  •  💬 {v['comentarios']:,}\n"
                    f"🔗 {v['url']}"
                }}],
                "icon": {"emoji": "▶️"},
                "color": "gray_background"
            }
        })

    return blocos


def salvar_coleta_no_notion(dados: dict, url_origem: str, max_retries: int = 3):
    """Cria entrada na database COLETAS YOUTUBE com thumbnail como capa."""
    top = dados.get("top_video") or {}

    # Usa thumbnail do top vídeo como capa (mais visual que o avatar do canal)
    capa_url = top.get("thumbnail") or dados.get("thumbnail_url") or ""

    properties = {
        "Canal":           {"title": [{"text": {"content": dados["nome"]}}]},
        "Mês":             {"select": {"name": MES_ATUAL_PT}},
        "Inscritos":       {"number": dados["inscritos"]},
        "Total Vídeos":    {"number": dados["total_videos"]},
        "Canal URL":       {"url": url_origem},
        "CATEGORIA":       {"select": {"name": "CONCORRÊNCIA"}},
    }

    if top:
        properties["Top Vídeo"]       = {"rich_text": [{"text": {"content": top["titulo"]}}]}
        properties["Top Vídeo Views"] = {"number": top["views"]}

    page_body = {
        "parent":     {"database_id": NOTION_COLETAS_DB_ID},
        "properties": properties,
        "children":   montar_blocos_videos(dados["videos"])
    }

    if capa_url:
        page_body["cover"] = {"type": "external", "external": {"url": capa_url}}

    for tentativa in range(1, max_retries + 1):
        try:
            notion.pages.create(**page_body)
            print(f"  ✓ Salvo com thumbnail: {dados['nome']} — {MES_ATUAL_PT}")
            return
        except APIResponseError as e:
            print(f"  ⚠ Erro Notion (tentativa {tentativa}/{max_retries}): {e}")
            if tentativa == max_retries:
                print(f"  ✗ Falha ao salvar {dados['nome']}. Continuando.")
                return
            time.sleep(2 ** tentativa)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n=== Coleta YouTube — {MES_ATUAL} ===\n")

    entradas = buscar_entradas_novas()

    if not entradas:
        print("Nenhuma entrada nova para coletar.")
        return

    print(f"{len(entradas)} entrada(s) encontrada(s).\n")

    for entrada in entradas:
        page_id = entrada["id"]

        # Extrair URL
        url_prop = entrada["properties"].get("URL", {}).get("url") or ""
        nome_prop = entrada["properties"].get("Name", {}).get("title", [])
        nome = nome_prop[0]["text"]["content"] if nome_prop else page_id

        print(f"Processando: {nome} ({url_prop})")

        if not url_prop:
            print("  ⚠ Sem URL, pulando.")
            marcar_processado(page_id)
            continue

        channel_id = extrair_channel_id(url_prop)
        if not channel_id:
            print(f"  ⚠ Não foi possível extrair channel ID de: {url_prop}")
            marcar_processado(page_id)
            continue

        dados = buscar_dados_canal(channel_id)
        if not dados:
            print(f"  ⚠ Canal não encontrado para ID: {channel_id}")
            marcar_processado(page_id)
            continue

        salvar_coleta_no_notion(dados, url_origem=url_prop)
        marcar_processado(page_id)

    print("\n=== Coleta concluída ===")


if __name__ == "__main__":
    main()
