import type { AttachmentMeta } from "../types";
import { humanSize } from "../lib/files";

// En el composer el adjunto trae los datos (data/media_type/format) → podemos mostrar miniatura.
// En los mensajes ya enviados solo llega el meta (name/kind/size) → mostramos ícono por tipo.
type ChipAttachment = AttachmentMeta & {
  data?: string;
  media_type?: string;
  format?: string;
};

function fileExt(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toUpperCase() : "";
}

function typeLabel(att: ChipAttachment): string {
  if (att.kind === "document") return "PDF";
  if (att.kind === "table") return (att.format || fileExt(att.name) || "CSV").toUpperCase();
  if (att.kind === "image") return fileExt(att.name) || "IMG";
  return fileExt(att.name) || "TXT";
}

// Variante de color del badge según el tipo de archivo.
function badgeKind(att: ChipAttachment): string {
  const e = (att.format || fileExt(att.name)).toLowerCase();
  if (att.kind === "document" || e === "pdf") return "pdf";
  if (e === "csv" || e === "tsv") return "csv";
  if (e === "xlsx" || e === "xls") return "xlsx";
  if (att.kind === "image") return "img";
  return "txt";
}

export function AttachmentChip({
  att,
  onRemove,
}: {
  att: ChipAttachment;
  onRemove?: () => void;
}) {
  const showThumb = att.kind === "image" && !!att.data;
  return (
    <div className="chip" title={att.name}>
      {showThumb ? (
        <img
          className="chip-thumb"
          src={`data:${att.media_type || "image/png"};base64,${att.data}`}
          alt=""
        />
      ) : (
        <span className={`chip-badge chip-badge-${badgeKind(att)}`} aria-hidden="true">
          {typeLabel(att)}
        </span>
      )}
      <span className="chip-name">{att.name}</span>
      <span className="chip-size">{humanSize(att.size)}</span>
      {onRemove && (
        <button className="chip-remove" onClick={onRemove} aria-label="Remove" type="button">
          &times;
        </button>
      )}
    </div>
  );
}
