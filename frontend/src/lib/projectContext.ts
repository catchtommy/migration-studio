import { createContext, useContext } from "react";

/** Switching the current project (the header switcher, or opening a project from the Projects page). */
export const ProjectSwitch = createContext<(key: string) => void>(() => {});

export const useSwitchProject = () => useContext(ProjectSwitch);

const STORAGE_KEY = "migration-studio.project";

export function rememberedProject(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function rememberProject(key: string) {
  try {
    localStorage.setItem(STORAGE_KEY, key);
  } catch {
    /* private window or blocked storage: the first project is used next time */
  }
}

/** Languages a project can be in, with the stacks suggested when one is picked (see backend settings_store). */
export const LANGUAGES: { key: string; label: string; source: string; target: string; toolchain: string }[] = [
  { key: "java", label: "Java", source: "Java 8, Spring MVC 3 and iBatis on Oracle, with JSP views", target: "Java 25 and Spring Boot microservices", toolchain: "JDK" },
  { key: "csharp", label: "C# / .NET", source: ".NET Framework 4.8, ASP.NET WebForms and Entity Framework 6 on SQL Server", target: ".NET 9 ASP.NET Core microservices", toolchain: ".NET SDK" },
  { key: "typescript", label: "TypeScript / Node.js", source: "Node.js 14 and Express with a monolithic database", target: "Node.js 22 and NestJS microservices", toolchain: "Node.js" },
  { key: "python", label: "Python", source: "Python 2.7 and Django 1.x", target: "Python 3.13 and FastAPI microservices", toolchain: "Python" },
  { key: "go", label: "Go", source: "A Go monolith", target: "Go microservices", toolchain: "Go" },
  { key: "other", label: "Other", source: "", target: "", toolchain: "toolchain" },
];
