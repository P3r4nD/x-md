# x-md

Script que descarga un artículo de X, lo convierte a Markdown (`<slug>/<slug>.md`), guarda sus imágenes en `<slug>/imgs/` y, opcionalmente, lo traduce con [LibreTranslate](https://docs.libretranslate.com/es/guides/installation/) (`<slug>-<IDIOMA>.md`).

Por defecto el `.html` intermedio se elimina tras generar el `.md` (`DELETE_HTML_AFTER_MD = True` en `xmd.py`; ponlo a `False` para conservarlo).

## Instalación

```bash
python3 -m venv venv
source venv/bin/activate        # fish: source venv/bin/activate.fish
pip install -r requirements.txt
```

`requirements.txt` incluye `libretranslate`, necesario solo para traducir.

## Uso

```bash
# Descargar artículo y generar el .md
python3 xmd.py URL_ARTICULO

# Descargar y traducir (p. ej. es)
python3 xmd.py URL_ARTICULO es

# Traducir un artículo ya descargado (carpeta/slug)
python3 xmd.py SLUG es

# Borrar los .html que hayan quedado en las carpetas de artículos
python3 xmd.py del-html
```

## Traducción (LibreTranslate)

Arranca el servidor en otra terminal, con el venv activo:

```bash
libretranslate --host 127.0.0.1 --port 5000
```

Por defecto se usa `http://127.0.0.1:5000`; para otra URL:

```bash
export LIBRETRANSLATE_URL=http://host:puerto   # fish: set -x LIBRETRANSLATE_URL ...
```

### Cargar nuevos idiomas

LibreTranslate descarga los modelos de idioma (Argos Translate) la primera vez que arranca y carga todos los disponibles por defecto. Para limitarlos o añadir otros:

```bash
# Cargar solo los idiomas indicados (códigos ISO separados por comas)
libretranslate --load-only en,es,fr,de

# Ver los idiomas cargados
curl http://127.0.0.1:5000/languages
```

Para instalar un modelo concreto manualmente (con el venv activo), por ejemplo en -> es:

```bash
argospm update
argospm install translate-en_es
```

Reinicia `libretranslate` tras instalar modelos nuevos. Los pares disponibles se listan con `argospm search`.
