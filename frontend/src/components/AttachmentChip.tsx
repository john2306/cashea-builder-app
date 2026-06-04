import type { AttachmentMeta } from "../types";
import { humanSize } from "../lib/files";

export function AttachmentChip({
  att,
  onRemove,
}: {
  att: AttachmentMeta;
  onRemove?: () => void;
}) {
  return (
    <div className="chip" title={att.name}>
      <span className={`chip-icon chip-icon-${att.kind}`} aria-hidden="true" />
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
