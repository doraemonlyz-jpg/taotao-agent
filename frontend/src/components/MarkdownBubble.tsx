import { useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github.css";

/**
 * Markdown renderer used inside chat bubbles.
 *
 * Why a wrapper instead of `<ReactMarkdown>` directly?
 *   1. We need a `Copy` button on every fenced code block.
 *   2. We need to override link targets to `_blank rel=noopener`.
 *   3. We strip the wrapping `<p>` for a single-line-text bubble so
 *      bubbles don't have an extra trailing margin.
 *
 * Streaming-safe: react-markdown re-parses on every render so the
 * incrementally-arriving `streaming` text just keeps re-rendering.
 */
interface Props {
  text: string;
  /** When true, suppress the bottom paragraph margin. */
  inline?: boolean;
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const onClick = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* ignore — clipboard API unavailable in some sandboxes */
    }
  };
  return (
    <button
      type="button"
      className="md-copy"
      onClick={onClick}
      title={copied ? "copied!" : "copy"}
    >
      {copied ? "✓ copied" : "copy"}
    </button>
  );
}

export default function MarkdownBubble({ text, inline = false }: Props) {
  return (
    <div className={`md ${inline ? "md-inline" : ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          // External links open in a new tab, never break the SPA.
          a: ({ href, children, ...rest }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              {...rest}
            >
              {children}
            </a>
          ),
          // Fenced code blocks: wrap with a <pre> + Copy button.
          // Inline `code` stays as a normal styled span (no copy button).
          pre: ({ children }: { children?: ReactNode }) => {
            // The child of <pre> is always a <code> element. We dig
            // into its props to extract the raw text for the clipboard.
            const codeEl: any = (children as any) ?? null;
            const codeText: string =
              typeof codeEl?.props?.children === "string"
                ? codeEl.props.children
                : Array.isArray(codeEl?.props?.children)
                ? codeEl.props.children.join("")
                : "";
            const lang =
              (codeEl?.props?.className || "")
                .split(" ")
                .find((c: string) => c.startsWith("language-"))
                ?.replace("language-", "") || "text";
            return (
              <div className="md-code">
                <div className="md-code-head">
                  <span className="md-code-lang">{lang}</span>
                  <CopyButton value={codeText.replace(/\n$/, "")} />
                </div>
                <pre>{children}</pre>
              </div>
            );
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
