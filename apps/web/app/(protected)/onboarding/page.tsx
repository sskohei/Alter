import { requireProfile } from "../../../lib/api-server";
import { OnboardingForm } from "../../../components/onboarding-form";
export default async function Onboarding() {
  const profile = await requireProfile();
  return <OnboardingForm owner={profile.id} />;
}
