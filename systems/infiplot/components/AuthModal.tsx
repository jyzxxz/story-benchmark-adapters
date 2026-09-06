"use client";

import { useCallback, useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { track } from "@/lib/analytics";
import { useI18n } from "@/lib/i18n/client";

type AuthStep = "pick" | "email-input" | "otp-verify";

export function AuthModal({
  onClose,
  onSuccess,
  onBeforeOAuth,
}: {
  onClose: () => void;
  onSuccess: () => void;
  // Fires synchronously before the OAuth full-page redirect (signInWithOAuth
  // navigates the browser away, unmounting the whole React tree). Hosts that
  // need to survive the round-trip (e.g. play page carrying in-memory game
  // state) snapshot into sessionStorage here — sessionStorage.setItem is
  // synchronous, so it completes before the navigation begins.
  onBeforeOAuth?: () => void;
}) {
  const [step, setStep] = useState<AuthStep>("pick");
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { t } = useI18n();

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const handleOAuth = useCallback(
    async (provider: "google" | "github") => {
      setLoading(true);
      setError("");
      // Snapshot before navigating away — the redirect below unmounts the app,
      // so any host state must be persisted to sessionStorage *now*.
      // Non-fatal: if the snapshot fails (e.g. sessionStorage is blocked in
      // privacy mode), the OAuth flow still proceeds — the user just won't
      // have their in-progress state restored on return.
      try {
        onBeforeOAuth?.();
      } catch {
        /* snapshot failure is non-fatal */
      }
      const supabase = createClient();
      const { error: oauthError } = await supabase.auth.signInWithOAuth({
        provider,
        options: {
          redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(window.location.pathname + window.location.search)}`,
        },
      });
      if (oauthError) {
        setError(oauthError.message);
        setLoading(false);
      }
    },
    [onBeforeOAuth],
  );

  const handleSendOtp = useCallback(async () => {
    const trimmed = email.trim();
    if (!trimmed) return;
    setLoading(true);
    setError("");
    const supabase = createClient();
    const { error: otpError } = await supabase.auth.signInWithOtp({
      email: trimmed,
    });
    setLoading(false);
    if (otpError) {
      setError(otpError.message);
    } else {
      setStep("otp-verify");
    }
  }, [email]);

  const handleVerifyOtp = useCallback(async () => {
    const trimmedOtp = otp.trim();
    if (!trimmedOtp) return;
    setLoading(true);
    setError("");
    const supabase = createClient();
    const { error: verifyError } = await supabase.auth.verifyOtp({
      email: email.trim(),
      token: trimmedOtp,
      type: "email",
    });
    setLoading(false);
    if (verifyError) {
      setError(verifyError.message);
    } else {
      track("login_success", { provider: "email" });
      onSuccess();
    }
  }, [email, otp, onSuccess]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center px-4"
      style={{ background: "rgba(0,0,0,0.55)" }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "rgba(14, 10, 6, 0.92)",
          border: "1.5px solid rgba(175, 138, 72, 0.72)",
          borderRadius: "8px",
          backdropFilter: "blur(14px)",
          WebkitBackdropFilter: "blur(14px)",
          boxShadow:
            "0 10px 42px rgba(0,0,0,0.62), inset 0 1px 0 rgba(200,165,90,0.12)",
        }}
        role="dialog"
        aria-modal="true"
        aria-label={t("auth.ariaLabel")}
      >
        {/* header */}
        <div className="flex items-center justify-between border-b border-cream-50/10 px-5 py-3.5">
          <div className="flex items-center gap-2 text-[11px] smallcaps text-cream-50/70">
            <i className="fa-solid fa-right-to-bracket text-[11px]" />
            {step === "pick" && t("auth.steps.pick")}
            {step === "email-input" && t("auth.steps.email")}
            {step === "otp-verify" && t("auth.steps.otp")}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center text-cream-50/60 transition-colors hover:text-cream-50"
            aria-label={t("auth.close")}
          >
            <i className="fa-solid fa-xmark text-[12px]" />
          </button>
        </div>

        <div className="px-5 py-5 space-y-3">
          {error && (
            <p className="text-[12px] text-red-400/90 leading-snug">{error}</p>
          )}

          {step === "pick" && (
            <>
              <button
                type="button"
                disabled={loading}
                onClick={() => handleOAuth("google")}
                className="flex w-full items-center justify-center gap-2.5 rounded-md border border-cream-50/15 bg-cream-50/[0.06] px-4 py-2.5 text-[13px] text-cream-50/90 transition-colors hover:bg-cream-50/[0.12] disabled:opacity-50"
              >
                <i className="fa-brands fa-google text-[14px]" />
                {t("auth.googleLogin")}
              </button>
              <button
                type="button"
                disabled={loading}
                onClick={() => handleOAuth("github")}
                className="flex w-full items-center justify-center gap-2.5 rounded-md border border-cream-50/15 bg-cream-50/[0.06] px-4 py-2.5 text-[13px] text-cream-50/90 transition-colors hover:bg-cream-50/[0.12] disabled:opacity-50"
              >
                <i className="fa-brands fa-github text-[14px]" />
                {t("auth.githubLogin")}
              </button>
              <div className="flex items-center gap-3 py-1">
                <div className="h-px flex-1 bg-cream-50/10" />
                <span className="text-[10px] text-cream-50/40">{t("auth.or")}</span>
                <div className="h-px flex-1 bg-cream-50/10" />
              </div>
              <button
                type="button"
                onClick={() => setStep("email-input")}
                className="flex w-full items-center justify-center gap-2.5 rounded-md border border-cream-50/15 bg-cream-50/[0.06] px-4 py-2.5 text-[13px] text-cream-50/90 transition-colors hover:bg-cream-50/[0.12]"
              >
                <i className="fa-solid fa-envelope text-[13px]" />
                {t("auth.emailLogin")}
              </button>
            </>
          )}

          {step === "email-input" && (
            <>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSendOtp()}
                placeholder={t("auth.emailPlaceholder")}
                autoFocus
                className="w-full rounded-md border border-cream-50/15 bg-cream-50/[0.06] px-3.5 py-2.5 text-[13px] text-cream-50/90 placeholder:text-cream-50/30 outline-none focus:border-[rgba(175,138,72,0.6)]"
              />
              <button
                type="button"
                disabled={loading || !email.trim()}
                onClick={handleSendOtp}
                className="w-full rounded-md bg-[rgba(175,138,72,0.85)] px-4 py-2.5 text-[13px] font-medium text-cream-50 transition-colors hover:bg-[rgba(175,138,72,1)] disabled:opacity-50"
              >
                {loading ? t("auth.sending") : t("auth.sendCode")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setStep("pick");
                  setError("");
                }}
                className="w-full text-center text-[12px] text-cream-50/50 transition-colors hover:text-cream-50/80"
              >
                {t("auth.back")}
              </button>
            </>
          )}

          {step === "otp-verify" && (
            <>
              <p className="text-[12px] text-cream-50/60 leading-snug">
                {t("auth.codeSent", { email: email.trim() })}
              </p>
              <input
                type="text"
                inputMode="numeric"
                maxLength={6}
                value={otp}
                onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
                onKeyDown={(e) => e.key === "Enter" && handleVerifyOtp()}
                placeholder={t("auth.codePlaceholder")}
                autoFocus
                className="w-full rounded-md border border-cream-50/15 bg-cream-50/[0.06] px-3.5 py-2.5 text-center text-[16px] tracking-[0.35em] text-cream-50/90 placeholder:text-cream-50/30 placeholder:tracking-normal outline-none focus:border-[rgba(175,138,72,0.6)]"
              />
              <button
                type="button"
                disabled={loading || otp.length < 6}
                onClick={handleVerifyOtp}
                className="w-full rounded-md bg-[rgba(175,138,72,0.85)] px-4 py-2.5 text-[13px] font-medium text-cream-50 transition-colors hover:bg-[rgba(175,138,72,1)] disabled:opacity-50"
              >
                {loading ? t("auth.verifying") : t("auth.verify")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setStep("email-input");
                  setOtp("");
                  setError("");
                }}
                className="w-full text-center text-[12px] text-cream-50/50 transition-colors hover:text-cream-50/80"
              >
                {t("auth.resend")}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
