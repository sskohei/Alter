// These labels describe AI interaction counts, not a human relationship.
export function relationshipLabel(count: number) {
  return count === 0 ? "初対面" : count <= 2 ? "顔見知り" : "よく交流する相手";
}
