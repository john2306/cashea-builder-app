import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Renderiza markdown de forma segura (sin HTML crudo -> sin riesgo de XSS).
// remark-gfm habilita tablas, listas de tareas, tachado y autolinks.
function MarkdownImpl({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Los enlaces se abren en pestaña nueva y de forma segura.
          a: ({ node: _node, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

// Memo: durante el streaming el texto cambia en cada token; solo re-renderiza
// cuando el contenido realmente cambia.
export const Markdown = memo(MarkdownImpl);
