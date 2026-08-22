import { promises as fs } from "fs";
import path from "path";

/**
 * The API reference is generated from `api/openapi.json` — the same committed
 * spec the SDKs are generated from and the contract test asserts against — so
 * the docs cannot describe an endpoint the API does not have. A hand-written
 * reference drifts the first week; this one fails the build instead.
 */

export type ApiField = {
  name: string;
  required: boolean;
  type: string;
  description?: string;
  location: "query" | "path" | "body";
};

export type ApiOperation = {
  id: string;
  method: string;
  path: string;
  summary: string;
  fields: ApiField[];
};

export type ApiGroup = {
  title: string;
  blurb: string;
  operations: ApiOperation[];
};

type Schema = {
  $ref?: string;
  type?: string;
  enum?: string[];
  items?: Schema;
  anyOf?: Schema[];
  minimum?: number;
  maximum?: number;
  description?: string;
  properties?: Record<string, Schema>;
  required?: string[];
};

type Parameter = {
  name: string;
  in: string;
  required?: boolean;
  description?: string;
  schema?: Schema;
};

type Operation = {
  summary?: string;
  description?: string;
  parameters?: Parameter[];
  requestBody?: { content?: Record<string, { schema?: Schema }> };
};

export type Spec = {
  paths?: Record<string, Record<string, Operation>>;
  components?: { schemas?: Record<string, Schema> };
};

/** Order matters: the first matching group wins, and the last one catches. */
const GROUPS: { title: string; blurb: string; match: (path: string) => boolean }[] = [
  {
    title: "Agent drop-ins",
    blurb: "The shapes an agent framework already knows how to call.",
    match: (p) => p.startsWith("/v1/"),
  },
  {
    title: "Search and research",
    blurb: "The full contract: ranked evidence, claims, and cited reports.",
    match: (p) => p === "/search" || p.startsWith("/research"),
  },
  {
    title: "Drill-down",
    blurb: "Resolve a citation to the evidence behind it.",
    match: (p) =>
      p.startsWith("/source") ||
      p.startsWith("/chunk") ||
      p.startsWith("/claim") ||
      p.startsWith("/graph"),
  },
  {
    title: "Operating and account",
    blurb: "Health, metrics, the machine-readable contract, and per-key usage.",
    match: () => true,
  },
];

/** A short human type: "string", "string[]", "raw | claims | full", "integer 1-50". */
export function describeType(schema: Schema | undefined): string {
  if (!schema) return "any";
  if (Array.isArray(schema.enum)) return schema.enum.join(" | ");
  if (schema.anyOf) {
    const parts = schema.anyOf
      .filter((entry) => entry.type !== "null")
      .map((entry) => describeType(entry));
    return [...new Set(parts)].join(" | ") || "any";
  }
  if (schema.type === "array") return `${describeType(schema.items)}[]`;
  if (schema.type === "integer" || schema.type === "number") {
    const min = schema.minimum;
    const max = schema.maximum;
    if (min !== undefined && max !== undefined) return `${schema.type} ${min}-${max}`;
  }
  return schema.type ?? "object";
}

function resolveRef(spec: Spec, ref: string): Schema | undefined {
  // Only local component refs exist in this spec.
  const name = ref.replace("#/components/schemas/", "");
  return spec.components?.schemas?.[name];
}

function bodyFields(spec: Spec, operation: Operation): ApiField[] {
  const content = operation.requestBody?.content?.["application/json"];
  if (!content?.schema) return [];
  const schema = content.schema.$ref ? resolveRef(spec, content.schema.$ref) : content.schema;
  if (!schema?.properties) return [];
  const required: string[] = schema.required ?? [];
  return Object.entries(schema.properties).map(([name, prop]) => ({
    name,
    required: required.includes(name),
    type: describeType(prop),
    description: plain(prop.description),
    location: "body" as const,
  }));
}

function paramFields(operation: Operation): ApiField[] {
  return (operation.parameters ?? []).map((param) => ({
    name: param.name,
    required: Boolean(param.required),
    type: describeType(param.schema),
    description: plain(param.description ?? param.schema?.description),
    location: param.in === "path" ? ("path" as const) : ("query" as const),
  }));
}

/** Field notes come from Python docstrings, so they carry markdown backticks
 *  that a table cell renders literally. The cell is already monospaced where it
 *  matters. */
function plain(text: string | undefined): string | undefined {
  return text?.replace(/`/g, "");
}

/** First sentence of the description: the reference lists operations, and the
 *  long form belongs on the endpoint's own docs page. */
export function firstSentence(text: string | undefined): string {
  if (!text) return "";
  const flat = text.replace(/\s+/g, " ").trim();
  const stop = flat.search(/\.\s|\.$/);
  return stop === -1 ? flat : flat.slice(0, stop + 1);
}

const METHODS = ["get", "post", "put", "patch", "delete"];

export function toReference(spec: Spec): ApiGroup[] {
  const groups: ApiGroup[] = GROUPS.map((group) => ({
    title: group.title,
    blurb: group.blurb,
    operations: [],
  }));

  for (const [route, item] of Object.entries(spec.paths ?? {})) {
    for (const method of METHODS) {
      const operation = item[method];
      if (!operation) continue;
      const index = GROUPS.findIndex((group) => group.match(route));
      groups[index].operations.push({
        id: `${method}-${route}`,
        method: method.toUpperCase(),
        path: route,
        summary: plain(firstSentence(operation.description ?? operation.summary)) ?? "",
        fields: [...paramFields(operation), ...bodyFields(spec, operation)],
      });
    }
  }

  for (const group of groups) {
    group.operations.sort(
      (a, b) => a.path.localeCompare(b.path) || a.method.localeCompare(b.method),
    );
  }
  return groups.filter((group) => group.operations.length > 0);
}

/** The build may run from web/ or from the repo root, depending on the host. */
const SPEC_PATHS = [
  path.join(process.cwd(), "..", "api", "openapi.json"),
  path.join(process.cwd(), "api", "openapi.json"),
];

export async function loadReference(): Promise<ApiGroup[] | null> {
  for (const file of SPEC_PATHS) {
    try {
      return toReference(JSON.parse(await fs.readFile(file, "utf8")));
    } catch {
      continue;
    }
  }
  return null;
}
