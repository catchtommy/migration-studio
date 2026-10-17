import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { DomainAvatar } from "../components/DomainAvatar";
import { FeatureForm } from "./DomainFeatures";

/** Editing one feature of a domain, on its own page (a long feature list keeps its place). */
export function FeaturePage() {
  const { domainKey = "", featureKey = "" } = useParams();
  const navigate = useNavigate();
  const { data: domain, error } = useQuery({ queryKey: ["domain", domainKey], queryFn: () => api.domain(domainKey) });
  const { data: features } = useQuery({ queryKey: ["features", domainKey], queryFn: () => api.features(domainKey), enabled: !!domain });
  const feature = features?.find((f) => f.key === featureKey);
  const back = `/domains/${domainKey}?tab=features#feature-${encodeURIComponent(featureKey)}`;

  if (error) return <p className="text-bad">{(error as Error).message}</p>;
  if (!domain || !features) return <p className="text-subtle">Loading…</p>;
  if (!feature)
    return (
      <p className="text-sm">
        {domain.name} has no feature {featureKey}.{" "}
        <Link to={`/domains/${domainKey}?tab=features`} className="text-accent hover:underline">
          Back to its features
        </Link>
      </p>
    );
  return (
    <div className="space-y-6">
      <div>
        <Link to={back} className="text-sm text-subtle hover:text-fg">
          ← {domain.name} features
        </Link>
        <div className="mt-3 flex items-center gap-3">
          <DomainAvatar name={domain.name} domainKey={domain.key} size="h-11 w-11 text-lg" />
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{feature.name}</h1>
            <code className="text-xs text-subtle">
              {domain.key} / {feature.key}
            </code>
          </div>
        </div>
      </div>
      <FeatureForm domain={domain} feature={feature} onDone={() => navigate(back)} />
    </div>
  );
}
