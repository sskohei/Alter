import { labels, publicFields, type AgentFormValues } from "../lib/agent-form";
export function AgentPreview({
  values,
  previous,
}: {
  values: AgentFormValues;
  previous?: AgentFormValues;
}) {
  return (
    <dl className="preview-list">
      {publicFields.map((field) => (
        <div key={field}>
          <dt>{labels[field]}</dt>
          <dd>
            {previous && previous[field] !== values[field] && (
              <>
                <span className="diff-label">変更前</span>
                <p className="diff-old">{previous[field] || "未入力"}</p>
                <span className="diff-label">変更後</span>
              </>
            )}
            {values[field] || "未入力"}
          </dd>
        </div>
      ))}
    </dl>
  );
}
