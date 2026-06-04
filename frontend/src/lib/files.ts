import type { Attachment } from "../types";

export const MAX_FILE_BYTES = 10 * 1024 * 1024; // 10 MB por archivo

// Extensiones que tratamos como texto aunque el MIME venga vacío o genérico.
const TEXT_EXT = new Set([
  "txt", "md", "markdown", "json", "yaml", "yml", "xml", "html",
  "css", "scss", "js", "jsx", "ts", "tsx", "py", "java", "go", "rb", "php",
  "rs", "c", "h", "cpp", "cs", "sh", "bash", "sql", "env", "ini", "toml", "log",
]);

// Planillas: se analizan con pandas en el backend (kind "table").
const TABLE_EXT = new Set(["csv", "tsv", "xlsx", "xls"]);

function ext(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = reader.result as string;
      resolve(url.split(",")[1] ?? ""); // quita el prefijo data:...;base64,
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export async function fileToAttachment(file: File): Promise<Attachment> {
  if (file.size > MAX_FILE_BYTES) {
    throw new Error(`"${file.name}" exceeds the 10 MB limit.`);
  }

  const isImage = file.type.startsWith("image/");
  const isPdf = file.type === "application/pdf" || ext(file.name) === "pdf";

  if (isImage) {
    return {
      kind: "image",
      name: file.name,
      size: file.size,
      media_type: file.type || "image/png",
      data: await readBase64(file),
    };
  }
  if (isPdf) {
    return {
      kind: "document",
      name: file.name,
      size: file.size,
      media_type: "application/pdf",
      data: await readBase64(file),
    };
  }
  if (TABLE_EXT.has(ext(file.name))) {
    return {
      kind: "table",
      name: file.name,
      size: file.size,
      format: ext(file.name),
      data: await readBase64(file),
    };
  }

  // Texto / código: si no es texto reconocible, lo intentamos igual como texto.
  const looksText = file.type.startsWith("text/") || TEXT_EXT.has(ext(file.name));
  if (!looksText && file.type) {
    throw new Error(`Unsupported file type: ${file.name} (${file.type}).`);
  }
  return {
    kind: "text",
    name: file.name,
    size: file.size,
    text: await file.text(),
  };
}
