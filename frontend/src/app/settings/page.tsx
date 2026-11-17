import { Suspense } from "react";
import { SettingsForm } from "@/components/SettingsForm";
import { Loading } from "@/components/ui";

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <SettingsForm />
    </Suspense>
  );
}
