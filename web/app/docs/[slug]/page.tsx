import type { Metadata } from "next";
import { notFound } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { DocsSidebar } from "@/components/DocsSidebar";
import { SiteNav } from "@/components/SiteNav";
import { DOC_PAGES, findDoc, loadDoc } from "@/lib/docs";

type Params = { params: Promise<{ slug: string }> };

export function generateStaticParams() {
  return DOC_PAGES.map((page) => ({ slug: page.slug }));
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const page = findDoc((await params).slug);
  return page
    ? { title: `${page.title} | moo docs`, description: page.blurb }
    : { title: "Not found | moo docs" };
}

export default async function DocPage({ params }: Params) {
  const { slug } = await params;
  const page = findDoc(slug);
  const markdown = page ? await loadDoc(slug) : null;
  if (!page || markdown === null) notFound();

  return (
    <div className="site">
      <SiteNav current="docs" />
      <div className="docs-layout">
        <DocsSidebar current={slug} />
        <main className="docs-main">
          <h1 className="page-title">{page.title}</h1>
          <article className="prose">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
          </article>
          <p className="docs-source">
            Source:{" "}
            <a href={`https://github.com/suppprith/moo/blob/main/docs/${page.file}`}>
              docs/{page.file}
            </a>
          </p>
        </main>
      </div>
    </div>
  );
}
