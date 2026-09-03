// Centralized API configuration.
//
// Every service module should import API_BASE_URL from here rather than
// hardcoding a host — that keeps exactly one place to change when the
// backend moves (e.g. a deployed URL) instead of scattered literals.
//
// VITE_API_BASE_URL is read from the environment at build time (see
// .env.example). No secrets belong in this file or in any VITE_* variable
// — Vite bundles them into the client-side JS, so they're public.
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
