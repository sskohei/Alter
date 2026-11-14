import { InteractionSettings } from "../../../../components/interaction-settings";
import { interactionDisclosure } from "../../../../lib/interaction-policy";
export default function InteractionPage() {
  return <InteractionSettings disclosure={interactionDisclosure()} />;
}
