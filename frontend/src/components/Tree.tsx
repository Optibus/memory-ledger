import type { ReactNode } from "react";
import "./Tree.css";

export interface TreeProps<T> {
  nodes: T[];
  /** Indentation carries ONE relationship and nothing else — see CONVENTIONS §5a. */
  childrenOf: (node: T) => T[] | undefined;
  keyOf: (node: T) => string;
  render: (node: T, depth: number) => ReactNode;
  depth?: number;
}

/** Renders a nested list where nesting means exactly one edge. */
export function Tree<T>({ nodes, childrenOf, keyOf, render, depth = 0 }: TreeProps<T>) {
  if (!nodes.length) return null;
  return (
    <ul className="ml-tree" data-depth={depth}>
      {nodes.map((node) => {
        const kids = childrenOf(node) ?? [];
        return (
          <li key={keyOf(node)} className="ml-tree__item">
            {render(node, depth)}
            {kids.length ? (
              <Tree nodes={kids} childrenOf={childrenOf} keyOf={keyOf} render={render} depth={depth + 1} />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
