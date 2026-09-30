import os
import shutil
import openpyxl
import requests


# ============================================================
# CONFIG
# ============================================================

SITE_URL = "https://topperprep.in"

API_KEY = "3f83b2edcc88537"
API_SECRET = "0241ecfaef81182"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

EXCEL_PATH = os.path.join(
    BASE_DIR,
    "file",
    "tarkik_kasoti.xlsx"
)

OUTPUT_EXCEL_PATH = os.path.join(
    BASE_DIR,
    "file",
    "28_Tarkik_Kasoti_with_images.xlsx"
)

TEMP_IMAGE_DIR = os.path.join(
    BASE_DIR,
    "_excel_images_tmp"
)

IS_PRIVATE = False

HEADERS = {
    "Authorization": f"token {API_KEY}:{API_SECRET}"
}


# ============================================================
# CREATE TEMP DIRECTORY
# ============================================================

def prepare_temp_directory():

    if os.path.exists(TEMP_IMAGE_DIR):
        shutil.rmtree(TEMP_IMAGE_DIR)

    os.makedirs(TEMP_IMAGE_DIR)


# ============================================================
# UPLOAD FILE TO ERPNEXT
# ============================================================

def upload_file(file_path):

    print(
        f"       Uploading: "
        f"{os.path.basename(file_path)}"
    )

    with open(file_path, "rb") as f:

        files = {
            "file": (
                os.path.basename(file_path),
                f
            )
        }

        data = {
            "is_private": 1 if IS_PRIVATE else 0
        }

        response = requests.post(
            f"{SITE_URL}/api/method/upload_file",
            headers=HEADERS,
            files=files,
            data=data,
            timeout=120
        )

    if response.status_code != 200:

        print(
            "       ERPNext error:"
        )

        print(
            response.text
        )

        response.raise_for_status()

    result = response.json()

    if "message" not in result:

        raise Exception(
            f"Invalid ERPNext response:\n{result}"
        )

    file_url = result["message"].get(
        "file_url"
    )

    if not file_url:

        raise Exception(
            f"No file_url returned:\n{result}"
        )

    return file_url


# ============================================================
# GET IMAGE ANCHOR CELL
# ============================================================

def get_image_cell(image):

    """
    Returns the Excel cell where the image is anchored.

    Example:

        C5

    """

    try:

        anchor = image.anchor

        # openpyxl OneCellAnchor
        if hasattr(anchor, "_from"):

            row = anchor._from.row + 1
            col = anchor._from.col + 1

            return row, col

        # Some versions expose row/col directly
        if hasattr(anchor, "row") and hasattr(anchor, "col"):

            row = anchor.row + 1
            col = anchor.col + 1

            return row, col

    except Exception as e:

        print(
            f"       [!] Cannot determine image cell: {e}"
        )

    return None, None


# ============================================================
# SAVE EMBEDDED EXCEL IMAGE
# ============================================================

def save_excel_image(image, index):

    """
    Extracts an openpyxl embedded image
    and saves it to disk.
    """

    try:

        image_data = image._data()

    except Exception:

        # Alternative for some openpyxl versions
        try:
            image_data = image.ref.read()

        except Exception as e:

            raise Exception(
                f"Cannot read Excel image: {e}"
            )

    # --------------------------------------------------------
    # Determine extension
    # --------------------------------------------------------

    image_format = getattr(
        image,
        "format",
        None
    )

    if image_format:

        extension = image_format.lower()

    else:

        extension = "png"

    if extension == "jpeg":
        extension = "jpg"

    filename = (
        f"excel_image_{index}.{extension}"
    )

    file_path = os.path.join(
        TEMP_IMAGE_DIR,
        filename
    )

    with open(
        file_path,
        "wb"
    ) as f:

        f.write(image_data)

    return file_path


# ============================================================
# SCAN ALL EMBEDDED IMAGES
# ============================================================

def process_embedded_images(ws):

    print(
        "\n2. Scanning ALL Excel images..."
    )

    images = getattr(
        ws,
        "_images",
        []
    )

    print(
        f"   Total embedded images found: "
        f"{len(images)}"
    )

    if not images:

        print(
            "\n   No embedded images found."
        )

        print(
            "   If your images are stored outside "
            "Excel as ZIP files, use the ZIP-based "
            "version instead."
        )

        return 0

    uploaded = 0

    # --------------------------------------------------------
    # Process every image
    # --------------------------------------------------------

    for index, image in enumerate(
        images,
        start=1
    ):

        print(
            "\n--------------------------------------------------"
        )

        print(
            f"Image #{index}"
        )

        # ----------------------------------------------------
        # Find image cell
        # ----------------------------------------------------

        row, col = get_image_cell(
            image
        )

        if not row or not col:

            print(
                "   [!] Could not determine cell."
            )

            continue

        cell = ws.cell(
            row=row,
            column=col
        )

        print(
            f"   Cell: {cell.coordinate}"
        )

        print(
            f"   Existing value: {cell.value}"
        )

        # ----------------------------------------------------
        # Save image
        # ----------------------------------------------------

        try:

            local_path = save_excel_image(
                image,
                index
            )

        except Exception as e:

            print(
                f"   [ERROR] Extracting image: {e}"
            )

            continue

        # ----------------------------------------------------
        # Upload to ERPNext
        # ----------------------------------------------------

        try:

            file_url = upload_file(
                local_path
            )

        except Exception as e:

            print(
                f"   [ERROR] Upload failed: {e}"
            )

            continue

        # ----------------------------------------------------
        # UPDATE EXACT CELL
        # ----------------------------------------------------

        cell.value = file_url

        print(
            f"   Updated {cell.coordinate}:"
        )

        print(
            f"   {file_url}"
        )

        uploaded += 1

    return uploaded


# ============================================================
# SCAN CELLS FOR EXISTING IMAGE PATHS
# ============================================================

def process_existing_file_paths(ws):

    """
    Scan every cell.

    If a cell already contains a local image path,
    upload it and replace it with ERPNext URL.

    This is useful if your Excel contains:

        /home/.../image.png
        image.png
        C:\\images\\image.png
    """

    print(
        "\n3. Scanning EVERY CELL for existing image paths..."
    )

    uploaded = 0

    image_extensions = (
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".bmp"
    )

    for row in ws.iter_rows():

        for cell in row:

            value = cell.value

            if not isinstance(
                value,
                str
            ):
                continue

            value = value.strip()

            if not value:
                continue

            # ------------------------------------------------
            # Only consider image-looking values
            # ------------------------------------------------

            lower_value = value.lower()

            if not lower_value.endswith(
                image_extensions
            ):
                continue

            # ------------------------------------------------
            # Check if actual local file exists
            # ------------------------------------------------

            possible_paths = [
                value,
                os.path.join(
                    BASE_DIR,
                    value
                ),
                os.path.join(
                    BASE_DIR,
                    "file",
                    value
                )
            ]

            local_path = None

            for path in possible_paths:

                if os.path.isfile(path):

                    local_path = path
                    break

            if not local_path:

                continue

            print(
                f"\n   Found image path in "
                f"{cell.coordinate}:"
            )

            print(
                f"       {local_path}"
            )

            try:

                file_url = upload_file(
                    local_path
                )

                cell.value = file_url

                print(
                    f"       Updated {cell.coordinate}"
                )

                print(
                    f"       {file_url}"
                )

                uploaded += 1

            except Exception as e:

                print(
                    f"       [ERROR] {e}"
                )

    return uploaded


# ============================================================
# SCAN ALL CELLS
# ============================================================

def scan_all_cells(ws):

    """
    Scan every row and every column.

    This does NOT assume any particular column
    contains images.
    """

    print(
        "\n4. Scanning every Excel cell..."
    )

    count = 0

    for row in range(
        1,
        ws.max_row + 1
    ):

        for col in range(
            1,
            ws.max_column + 1
        ):

            cell = ws.cell(
                row=row,
                column=col
            )

            if cell.value is not None:

                count += 1

                print(
                    f"   Checking {cell.coordinate}: "
                    f"{str(cell.value)[:80]}"
                )

    print(
        f"\n   Total non-empty cells checked: "
        f"{count}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        "TOPPERPREP EXCEL IMAGE IMPORTER"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # Prepare
    # --------------------------------------------------------

    prepare_temp_directory()

    # --------------------------------------------------------
    # Open Excel
    # --------------------------------------------------------

    print(
        "\n1. Opening Excel..."
    )

    wb = openpyxl.load_workbook(
        EXCEL_PATH
    )

    ws = wb.active

    print(
        f"   Sheet: {ws.title}"
    )

    print(
        f"   Rows: {ws.max_row}"
    )

    print(
        f"   Columns: {ws.max_column}"
    )

    # --------------------------------------------------------
    # IMPORTANT
    #
    # First process actual embedded images.
    # --------------------------------------------------------

    embedded_uploaded = process_embedded_images(
        ws
    )

    # --------------------------------------------------------
    # Process cells that already contain image paths.
    # --------------------------------------------------------

    path_uploaded = process_existing_file_paths(
        ws
    )

    # --------------------------------------------------------
    # Scan every cell.
    # --------------------------------------------------------

    scan_all_cells(
        ws
    )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    print(
        "\n5. Saving updated Excel..."
    )

    wb.save(
        OUTPUT_EXCEL_PATH
    )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print(
        "\n" + "=" * 70
    )

    print(
        "COMPLETED"
    )

    print(
        "=" * 70
    )

    print(
        f"Embedded images uploaded : "
        f"{embedded_uploaded}"
    )

    print(
        f"Existing path images      : "
        f"{path_uploaded}"
    )

    print(
        f"Total uploaded            : "
        f"{embedded_uploaded + path_uploaded}"
    )

    print(
        "\nOutput:"
    )

    print(
        OUTPUT_EXCEL_PATH
    )

    print(
        "=" * 70
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()

