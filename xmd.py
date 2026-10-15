#!/usr/bin/env python3
"""Uso: python3 xmd.py URL_ARTICULO [IDIOMA] | SLUG [IDIOMA] | del-html"""

import mimetypes
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify
from slugify import slugify

HEADERS = {"User-Agent": "Wget/1.21.4"}
TIMEOUT = 30
TRANSLATE_URL = os.environ.get("LIBRETRANSLATE_URL", "http://127.0.0.1:5000")
TRANSLATE_TIMEOUT = 120
MAX_TRANSLATE_CHARS = 4000
DELETE_HTML_AFTER_MD = True  # borra el .html una vez generado el .md


def download_html(url):
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return response.content.decode(response.encoding or "utf-8", errors="ignore")


def clean_x_html(html):
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    return soup


def remove_engagement_stats(soup):
    """Elimina el bloque de respuestas/reposts/likes/vistas bajo el título."""
    h1 = soup.find("h1")
    if not h1:
        return

    stat = h1.find_next(attrs={"data-engagement-action": True})
    if not stat:
        return

    block = stat
    while block.parent is not None and block.parent is not h1.parent:
        block = block.parent

    if block.parent is h1.parent:
        block.decompose()


def extract_article_title(soup):
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(" ", strip=True)

    title = soup.find("title")
    if title:
        return title.get_text(strip=True)

    return "article"


def extract_article_body(soup):
    h1 = soup.find("h1")
    if not h1:
        return None

    container = h1.parent

    for _ in range(5):
        if container and len(container.get_text(" ", strip=True)) > 3000:
            return container

        if container:
            container = container.parent

    return h1.parent


def make_slug(title, url):
    slug = slugify(title)
    if slug:
        return slug

    status_id = re.search(r"/status/(\d+)", url)
    return status_id.group(1) if status_id else None


def image_extension(url, content_type):
    fmt = parse_qs(urlparse(url).query).get("format")
    if fmt:
        return "." + fmt[0]

    suffix = Path(urlparse(url).path).suffix
    if suffix:
        return suffix

    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
    return guessed or ".jpg"


def download_images(body, imgs_dir):
    """Descarga las imágenes del cuerpo y reescribe su src a imgs/<fichero>."""
    used = set()

    for img in body.find_all("img"):
        src = img.get("src", "")
        if not src.startswith("http"):
            continue

        try:
            response = requests.get(src, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"Aviso: no se pudo descargar {src}: {e}", file=sys.stderr)
            continue

        stem = Path(urlparse(src).path).stem or "img"
        ext = image_extension(src, response.headers.get("Content-Type"))
        name = stem + ext

        n = 1
        while name in used:
            n += 1
            name = f"{stem}-{n}{ext}"
        used.add(name)

        imgs_dir.mkdir(parents=True, exist_ok=True)
        (imgs_dir / name).write_bytes(response.content)

        img["src"] = f"imgs/{name}"

    return len(used)


def html_to_markdown(node):
    return markdownify(
        str(node),
        heading_style="ATX",
        bullets="-",
        strip=["script", "style"],
    )


def translate_chunk(text, target_language):
    inline = re.compile(r"`[^`\n]*`|\]\([^)]+\)|https?://[^\s)]+|<[^>\n]+>")
    prefix_re = re.compile(
        r"^(\s*(?:(?:#{1,6}|>|[-*+]|\d+[.)])\s+)*)(.*?)(\s*)$", re.DOTALL
    )

    lines = text.split("\n")
    parts = []
    for line in lines:
        prefix, body, suffix = prefix_re.match(line).groups()
        protected = []

        def replace_match(match, protected=protected):
            token = f"XMDKEEP{len(protected)}TOKENX"
            protected.append((token, match.group(0)))
            return token

        parts.append((prefix, inline.sub(replace_match, body), suffix, protected))

    indexes = [i for i, p in enumerate(parts) if p[1].strip()]
    if not indexes:
        return text

    try:
        response = requests.post(
            f"{TRANSLATE_URL.rstrip('/')}/translate",
            json={
                "q": [parts[i][1] for i in indexes],
                "source": "auto",
                "target": target_language.lower(),
                "format": "text",
            },
            timeout=TRANSLATE_TIMEOUT,
        )
        if not response.ok:
            raise RuntimeError(
                f"LibreTranslate devolvió {response.status_code}: {response.text}"
            )
    except requests.ConnectionError as error:
        raise RuntimeError(
            "No se pudo conectar con LibreTranslate en "
            f"{TRANSLATE_URL}. Inícialo antes de traducir."
        ) from error

    results = response.json()["translatedText"]
    out = list(lines)
    for i, translated in zip(indexes, results):
        prefix, _, suffix, protected = parts[i]
        for token, original in protected:
            if token not in translated:
                raise RuntimeError(
                    "LibreTranslate modificó el formato protegido del Markdown; "
                    "no se guardó la traducción."
                )
            translated = translated.replace(token, original)
        out[i] = prefix + translated + suffix
    return "\n".join(out)


def split_translation_chunks(text):
    while len(text) > MAX_TRANSLATE_CHARS:
        boundary = text.rfind("\n", 0, MAX_TRANSLATE_CHARS)
        if boundary < MAX_TRANSLATE_CHARS // 2:
            boundary = text.rfind(" ", 0, MAX_TRANSLATE_CHARS)
        if boundary < MAX_TRANSLATE_CHARS // 2:
            boundary = MAX_TRANSLATE_CHARS
        else:
            boundary += 1
        yield text[:boundary]
        text = text[boundary:]
    if text:
        yield text


def translate_markdown(markdown, target_language):
    translated = []
    text_block = []
    fence = None

    def flush_text_block():
        if not text_block:
            return
        block = "".join(text_block)
        text_block.clear()
        for chunk in split_translation_chunks(block):
            translated.append(translate_chunk(chunk, target_language))

    for line in markdown.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            flush_text_block()
            marker = stripped[:3]
            translated.append(line)
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
        elif fence is not None:
            translated.append(line)
        elif line.strip():
            text_block.append(line)
        else:
            flush_text_block()
            translated.append(line)

    flush_text_block()
    return "".join(translated)


def save_original(url, html):
    soup = clean_x_html(html)
    remove_engagement_stats(soup)

    title = extract_article_title(soup)
    slug = make_slug(title, url)
    if not slug:
        raise RuntimeError("No se pudo generar el slug del artículo")

    body = extract_article_body(soup)
    if not body:
        raise RuntimeError("No se encontró el cuerpo del artículo")

    folder = Path(slug)
    folder.mkdir(exist_ok=True)

    html_path = folder / f"{slug}.html"
    html_path.write_text(str(soup), encoding="utf-8")
    print(f"Generado: {html_path}")

    h1 = body.find("h1")
    if h1:
        h1.decompose()

    count = download_images(body, folder / "imgs")
    if count:
        print(f"Imágenes: {count} en {folder / 'imgs'}")

    md = html_to_markdown(body)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    md_path = folder / f"{slug}.md"
    md_path.write_text(f"# {title}\n\n{md}\n", encoding="utf-8")
    print(f"Generado: {md_path}")

    if DELETE_HTML_AFTER_MD:
        html_path.unlink()
        print(f"Eliminado: {html_path}")
    return slug, md_path


def delete_html():
    """Borra los <slug>/<slug>.html generados en el directorio actual."""
    count = 0
    for folder in sorted(Path(".").iterdir()):
        html_path = folder / f"{folder.name}.html"
        if folder.is_dir() and html_path.is_file():
            html_path.unlink()
            print(f"Eliminado: {html_path}")
            count += 1
    print(f"{count} fichero(s) HTML eliminados")


def main():
    if len(sys.argv) not in (2, 3):
        print(
            f"Uso: {sys.argv[0]} URL_ARTICULO [IDIOMA] | SLUG [IDIOMA] | del-html"
        )
        sys.exit(1)

    if sys.argv[1] == "del-html" and len(sys.argv) == 2:
        delete_html()
        return

    article_ref = sys.argv[1]
    target_language = sys.argv[2] if len(sys.argv) == 3 else None
    parsed = urlparse(article_ref)

    if parsed.scheme in ("http", "https"):
        html = download_html(article_ref)
        soup = clean_x_html(html)
        title = extract_article_title(soup)
        slug = make_slug(title, article_ref)
        if not slug:
            raise RuntimeError("No se pudo generar el slug del artículo")
        md_path = Path(slug) / f"{slug}.md"
        if md_path.exists():
            print(f"Original ya guardado: {md_path}")
        else:
            slug, md_path = save_original(article_ref, html)
    else:
        folder = Path(article_ref)
        slug = folder.name
        md_path = folder / f"{slug}.md"
        if not md_path.is_file():
            raise FileNotFoundError(f"No se encontró el original: {md_path}")
        print(f"Original ya guardado: {md_path}")

    if target_language:
        translated_path = md_path.with_name(
            f"{slug}-{target_language.upper()}.md"
        )
        if translated_path.exists():
            print(f"Traducción ya guardada: {translated_path}")
            return

        original = md_path.read_text(encoding="utf-8")
        translated = translate_markdown(original, target_language)
        translated_path.write_text(translated, encoding="utf-8")
        print(f"Generado: {translated_path}")


if __name__ == "__main__":
    main()
