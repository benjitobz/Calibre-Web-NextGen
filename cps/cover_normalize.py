# CWA-NG service: give every calibre cover the same canvas.
#
# Source covers arrive at whatever size and aspect the provider or the uploaded
# book happened to carry (334x500, 997x1500, 375x500, ...). Library grids render
# covers in a fixed 2:3 frame and crop to fill, so a squatter cover loses its top
# and bottom. Normalizing to one 2:3 canvas with padding keeps the whole cover
# visible and makes the shelf uniform.
#
# Chaptarr's CalibreCoverNormalizer targets the same 600x900 canvas, but it skips
# progressive JPEGs because ImageSharp 3.1.12 mis-decodes them (SOF2) and would
# corrupt the image. Practically every provider cover is progressive, so that
# guard means the C# side normalizes almost nothing. Pillow decodes progressive
# JPEGs correctly, so doing it here is what actually makes the library uniform.
import os

try:
    from PIL import Image
except Exception:  # pragma: no cover - Pillow ships with the image
    Image = None

TARGET_WIDTH = 600
TARGET_HEIGHT = 900
PAD_COLOR = (0, 0, 0)


def normalize_cover(path):
    """Resize a cover to the shared 600x900 canvas, padding rather than cropping
    so no artwork is lost. Returns True if the file was rewritten."""
    if Image is None or not os.path.exists(path):
        return False

    try:
        with Image.open(path) as im:
            if im.size == (TARGET_WIDTH, TARGET_HEIGHT):
                return False

            im = im.convert('RGB')
            src_w, src_h = im.size

            if src_w <= 0 or src_h <= 0:
                return False

            scale = min(TARGET_WIDTH / src_w, TARGET_HEIGHT / src_h)
            new_w = max(1, int(round(src_w * scale)))
            new_h = max(1, int(round(src_h * scale)))
            resized = im.resize((new_w, new_h), Image.LANCZOS)

            canvas = Image.new('RGB', (TARGET_WIDTH, TARGET_HEIGHT), PAD_COLOR)
            canvas.paste(resized, ((TARGET_WIDTH - new_w) // 2, (TARGET_HEIGHT - new_h) // 2))

        tmp = path + '.normalize'
        canvas.save(tmp, 'JPEG', quality=90)
        os.replace(tmp, path)
        return True
    except Exception:
        # A cover-shaping problem must never block the embed sweep.
        try:
            if os.path.exists(path + '.normalize'):
                os.remove(path + '.normalize')
        except Exception:
            pass
        return False
