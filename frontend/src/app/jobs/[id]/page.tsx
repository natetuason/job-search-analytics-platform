import { Suspense } from "react";
import { JobDetail } from "@/components/JobDetail";
import { Loading } from "@/components/ui";

export default function JobPage() {
  return (
    <Suspense fallback={<Loading />}>
      <JobDetail />
    </Suspense>
  );
}
