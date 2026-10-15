import SwaggerUI from "swagger-ui-react";
import "swagger-ui-react/swagger-ui.css";

/** An OpenAPI document in Swagger UI. Read-only: the services it describes aren't running, so "Try it out" is off. */
export default function SwaggerView({ spec }: { spec: string }) {
  return <SwaggerUI spec={spec} supportedSubmitMethods={[]} docExpansion="list" defaultModelsExpandDepth={0} />;
}
