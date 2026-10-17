// SPDX-License-Identifier: Apache-2.0
import { ArrowDown, ArrowRight } from "lucide-react";
import type { BlogWorkflow as Workflow } from "./blogData";

export default function BlogWorkflow({ workflow }: { workflow: Workflow }) {
  return (
    <figure className="blog-workflow" aria-labelledby={`${workflow.id}-title`}>
      <figcaption>
        <h3 id={`${workflow.id}-title`}>{workflow.title}</h3>
        <p className="workflow-status">{workflow.status}</p>
      </figcaption>
      <ol className="workflow-steps">
        {workflow.steps.map((step, index) => (
          <li key={step.title} data-owner={step.owner}>
            <p className="workflow-owner"><span>{index + 1}.</span> {step.owner}</p>
            <h4>{step.title}</h4>
            <p className="workflow-detail">{step.detail}</p>
            {index < workflow.steps.length - 1 && (
              <span className="workflow-next" aria-hidden="true">
                <ArrowRight className="workflow-arrow-right" size={18} />
                <ArrowDown className="workflow-arrow-down" size={18} />
              </span>
            )}
          </li>
        ))}
      </ol>
      <p className="workflow-hold"><strong>Keep the hold:</strong> {workflow.hold}</p>
      <p className="workflow-note">{workflow.note}</p>
    </figure>
  );
}
