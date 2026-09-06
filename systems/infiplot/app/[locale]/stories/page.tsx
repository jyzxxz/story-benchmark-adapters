"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { loadStoryList, deleteStory } from "@/lib/clientStoryPersistence";
import type { StoryMeta } from "@/lib/persistence/types";
import { coerceEpoch } from "@/lib/persistence/types";
import { useLocalePath } from "@/lib/i18n/hooks";
import { useI18n } from "@/lib/i18n/client";

export default function StoriesPage() {
  const lp = useLocalePath();
  const { t, locale } = useI18n();
  const [stories, setStories] = useState<StoryMeta[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    loadStoryList()
      .then(setStories)
      .catch(() => setStories([]))
      .finally(() => setLoading(false));
  }, []);

  const handleDelete = async (storyId: string) => {
    if (!confirm(t("stories.deleteConfirm"))) return;

    setDeletingId(storyId);
    const success = await deleteStory(storyId);

    if (success) {
      setStories((prev) => prev.filter((s) => s.id !== storyId));
    } else {
      alert(t("stories.deleteFailed"));
    }

    setDeletingId(null);
  };

  // Story timestamps cross the storage boundary as epoch ms (the local store
  // coerces them); coerceEpoch is the shared guard for any legacy string/Date.
  const formatDate = (value: Date | string | number) => {
    const ms = coerceEpoch(value, NaN);
    if (Number.isNaN(ms)) return "";
    const date = new Date(ms);

    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));

    if (days === 0) return t("stories.today");
    if (days === 1) return t("stories.yesterday");
    if (days < 7) return t("stories.daysAgo", { days });

    return date.toLocaleDateString(locale, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  };

  return (
    <div className="min-h-screen flex flex-col">
      {/* ================== HEADER ================== */}
      <header className="px-6 md:px-16 pt-7 md:pt-10 flex items-center justify-between">
        <Link
          href={lp("/")}
          className="text-[10px] smallcaps text-clay-700 hover:text-clay-900 transition-colors flex items-center gap-2 cursor-pointer"
        >
          <i className="fa-solid fa-arrow-left text-[9px]" />
          InfiPlot
        </Link>
        <span className="text-[10px] smallcaps text-clay-500">
          {t("stories.title")}
        </span>
      </header>

      {/* ================== CONTENT ================== */}
      <section className="px-6 md:px-16 pt-16 md:pt-24 pb-20 md:pb-24 flex-1">
        {loading ? (
          <div className="flex items-center justify-center min-h-[40vh]">
            <p className="text-[10px] smallcaps text-clay-500 animate-slow-pulse">
              {t("stories.loading")}
            </p>
          </div>
        ) : stories.length === 0 ? (
          <div className="flex flex-col items-center justify-center min-h-[40vh] text-center">
            <i className="fa-solid fa-book-open text-4xl text-clay-300 mb-6" />
            <p className="font-serif italic text-lg text-clay-500 mb-4">
              {t("stories.emptyTitle")}
            </p>
            <Link
              href={lp("/")}
              className="text-[10px] smallcaps text-clay-700 hover:text-ember-500 transition-colors cursor-pointer"
            >
              {t("stories.emptyBack")}
            </Link>
          </div>
        ) : (
          <div className="max-w-6xl mx-auto">
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {stories.map((story) => (
                <div
                  key={story.id}
                  className="bg-cream-100 border border-clay-900/10 rounded-sm p-6 transition-all duration-200 hover:shadow-md hover:border-clay-900/20 relative group"
                >
                  <Link
                    href={lp(`/play?storyId=${encodeURIComponent(story.id)}`)}
                    className="block cursor-pointer"
                  >
                    <div className="mb-4">
                      <h3 className="font-serif text-lg text-clay-900 leading-tight mb-2 line-clamp-2">
                        {story.worldSetting.slice(0, 60)}
                        {story.worldSetting.length > 60 ? "..." : ""}
                      </h3>
                      <p className="text-sm text-clay-600 line-clamp-1">
                        {story.styleGuide}
                      </p>
                    </div>

                    <div className="flex items-center gap-3 text-[10px] smallcaps text-clay-500">
                      <span className="flex items-center gap-1">
                        <i className="fa-solid fa-photo-film text-[9px]" />
                        {t("stories.scenes", { count: story.sceneCount })}
                      </span>
                      <span className="flex items-center gap-1">
                        <i className="fa-solid fa-clock text-[9px]" />
                        {formatDate(story.updatedAt)}
                      </span>
                    </div>
                  </Link>

                  {/* Delete button */}
                  <button
                    type="button"
                    onClick={(e) => {
                      e.preventDefault();
                      handleDelete(story.id);
                    }}
                    disabled={deletingId === story.id}
                    aria-label={t("stories.deleteLabel")}
                    className="absolute top-4 right-4 opacity-0 group-hover:opacity-100 transition-opacity text-clay-400 hover:text-ember-500 disabled:opacity-50 cursor-pointer"
                  >
                    <i className={deletingId === story.id ? "fa-solid fa-spinner fa-spin" : "fa-solid fa-trash-can"} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {/* ================== FOOTER ================== */}
      <footer className="px-6 md:px-16 pb-8">
        <div className="hairline-full w-full mb-4" />
        <div className="flex items-center justify-between text-[10px] smallcaps text-clay-500">
          <span>MMXXVI</span>
          <span className="num">{t("stories.storiesCount", { count: stories.length })}</span>
        </div>
      </footer>
    </div>
  );
}
