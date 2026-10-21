// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
import type { ReactNode } from "react";
import type { BlogDiagramId, BlogFigure as Figure } from "./blogData";
import { FlowFigure, HubPanel, JourneyFigure, RevocationFigure, WiredPanel } from "@/components/figures/OacpDiagrams";
import "@/components/figures/figureKit.css";

const DIAGRAMS: Record<BlogDiagramId, () => ReactNode> = {
  "oacp-hub": () => (
    <div className="fig-pair">
      <WiredPanel />
      <HubPanel />
    </div>
  ),
  "oacp-flow": () => <FlowFigure />,
  "oacp-journey": () => <JourneyFigure />,
  "oacp-revocation": () => <RevocationFigure />,
};

export default function BlogFigure({ figure }: { figure: Figure }) {
  const titleId = `${figure.id}-title`;
  return (
    <figure className={`blog-figure${figure.table ? " blog-figure-table" : ""}`} aria-labelledby={titleId} data-figure={figure.id}>
      <figcaption>
        <h3 id={titleId}>{figure.title}</h3>
        <p className="figure-caption">{figure.caption}</p>
      </figcaption>
      {figure.diagram && <div className="figure-canvas">{DIAGRAMS[figure.diagram]()}</div>}
      {figure.table && (
        <table className="figure-table">
          <thead>
            <tr>{figure.table.columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr>
          </thead>
          <tbody>
            {figure.table.rows.map((row) => (
              <tr key={row[0]}>
                <th scope="row">{row[0]}</th>
                {row.slice(1).map((cell, i) => <td key={cell} data-label={figure.table!.columns[i + 1]}>{cell}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </figure>
  );
}
