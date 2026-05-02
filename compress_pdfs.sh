#!/bin/bash

INPUT_DIR="$1"
OUTPUT_DIR="$2"

if [ -z "$INPUT_DIR" ] || [ -z "$OUTPUT_DIR" ]; then
  echo "Usage: ./compress_pdfs.sh <input_dir> <output_dir>"
  exit 1
fi

# Find all PDFs recursively
find "$INPUT_DIR" -type f -iname "*.pdf" | while read -r file; do

  # Get relative path
  rel_path="${file#$INPUT_DIR/}"

  # Output file path
  out_file="$OUTPUT_DIR/$rel_path"

  # Create target directory
  mkdir -p "$(dirname "$out_file")"

  echo "Processing: $rel_path"

  # Compress with Ghostscript
  gs -sDEVICE=pdfwrite \
    -dCompatibilityLevel=1.5 \
    -dPDFSETTINGS=/ebook \
    -dColorImageResolution=300 \
    -dGrayImageResolution=300 \
    -dMonoImageResolution=300 \
    -dColorConversionStrategy=/Gray \
    -dProcessColorModel=/DeviceGray \
    -dNOPAUSE -dQUIET -dBATCH \
    -sOutputFile="$out_file" "$file"

done

echo "Done!"