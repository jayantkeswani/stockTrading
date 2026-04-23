"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

const components: Components = {
  h1: ({ children }) => (
    <div className="text-[11px] font-mono text-text-primary uppercase tracking-wider mt-3 mb-1 font-medium">
      {children}
    </div>
  ),
  h2: ({ children }) => (
    <div className="text-[11px] font-mono text-text-primary uppercase tracking-wider mt-2 mb-1 font-medium">
      {children}
    </div>
  ),
  h3: ({ children }) => (
    <div className="text-[11px] font-mono text-text-secondary uppercase tracking-wider mt-1.5 mb-0.5">
      {children}
    </div>
  ),
  p: ({ children }) => (
    <p className="leading-relaxed">{children}</p>
  ),
  strong: ({ children }) => (
    <span className="text-text-primary font-medium">{children}</span>
  ),
  em: ({ children }) => (
    <span className="text-accent">{children}</span>
  ),
  ul: ({ children }) => (
    <ul className="list-disc list-inside space-y-0.5 pl-1">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="list-decimal list-inside space-y-0.5 pl-1">{children}</ol>
  ),
  li: ({ children }) => (
    <li className="leading-relaxed">{children}</li>
  ),
  code: ({ children, className }) => {
    const isBlock = className?.startsWith("language-");
    if (isBlock) {
      return (
        <pre className="bg-bg-tertiary border border-border rounded p-2 overflow-x-auto my-1">
          <code className="font-mono">{children}</code>
        </pre>
      );
    }
    return (
      <code className="bg-bg-tertiary text-accent px-1 rounded font-mono">
        {children}
      </code>
    );
  },
  pre: ({ children }) => <>{children}</>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-accent underline-offset-2 hover:underline"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-border pl-2 text-text-secondary italic">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-border my-2" />,
};

export function Markdown({ children }: { children: string | null | undefined }) {
  if (!children) return null;
  return (
    <div className="text-[11px] font-mono text-text-secondary leading-relaxed space-y-1.5">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
