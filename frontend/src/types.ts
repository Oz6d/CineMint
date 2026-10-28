export interface Counts {
  total: number;
  works: number;
  matched: number;
  review: number;
  unmatched: number;
  organized: number;
  failed: number;
  ignored: number;
  missing: number;
  pending: number;
}

export interface Library {
  id: number;
  name: string;
  source_id: number;
  source_name: string;
  source_type: string;
  root_path: string;
  target_root: string;
  media_type: string;
  naming: Record<string, unknown>;
  scrape: Record<string, unknown>;
  enabled: boolean;
  is_demo: boolean;
  last_scan_at: string | null;
  created_at: string;
  counts: Counts;
  posters: string[];
}

export interface ParsedName {
  title?: string;
  title_alt?: string;
  year?: number | null;
  season?: number | null;
  episode?: number | null;
  episode_end?: number | null;
  resolution?: string;
  video?: string;
  audio?: string;
  source?: string;
  edition?: string;
  group?: string;
  media_type?: string;
  is_extra?: boolean;
  confidence?: number;
  raw?: string;
}

export interface Attachment {
  name: string;
  path: string;
  kind: "subtitle" | "audio" | "image" | "meta" | string;
  kind_label: string;
  size: number;
  scope: "file" | "folder" | string;
  reason?: string;
  manual?: boolean;
}

export interface AttachmentVideo {
  path: string;
  name: string;
  season?: number | null;
  episode?: number | null;
}

export interface AttachmentPayload {
  items: Attachment[];
  folder_items: Attachment[];
  unmatched: Attachment[];
  counts: Record<string, number>;
  total: number;
  current_path?: string;
  videos?: AttachmentVideo[];
  error?: string;
}

export interface WorkSeason {
  season_number: number;
  name?: string;
  episode_count?: number;
  poster_path?: string;
  poster_url?: string;
  air_date?: string;
}

export interface Candidate {
  tmdb_id: number;
  media_type: "movie" | "tv";
  title: string;
  original_title?: string;
  year?: number | null;
  overview?: string;
  poster_path?: string;
  backdrop_path?: string;
  logo_path?: string;
  poster_url?: string;
  backdrop_url?: string;
  logo_url?: string;
  vote_average?: number;
  vote_count?: number;
  popularity?: number;
  genres?: string[];
  score?: number;
  ratio_to_top1?: number;
  selected?: boolean;
  confidence?: number | null;
  seasons?: WorkSeason[];
  number_of_seasons?: number;
  number_of_episodes?: number;
  cast?: { name: string; role?: string; profile_path?: string }[];
  runtime?: number | null;
  status?: string;
}

export interface MediaItem {
  id: number;
  library_id: number;
  library_name?: string;
  file_name: string;
  source_path: string;
  size: number;
  status: string;
  error?: string | null;
  parsed: ParsedName;
  match?: Candidate | null;
  candidates?: Candidate[];
  manual?: boolean;
  target_path?: string | null;
  target_name_override?: string;
  planned_name?: string;
  updated_at?: string;
}

export interface LibraryFileEntry {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  mtime: number;
  modified_at: string | null;
  kind: "folder" | "video" | "subtitle" | "audio" | "image" | "meta" | "other" | string;
  extension: string;
  is_symlink?: boolean;
}

export interface LibraryFileList {
  root_path: string;
  path: string;
  parent: string | null;
  entries: LibraryFileEntry[];
  counts: { files: number; dirs: number; size: number };
}

export interface FileMatchResult {
  path: string;
  pattern: string;
  mode: string;
  recursive: boolean;
  matches: LibraryFileEntry[];
  total: number;
  scanned: number;
  truncated: boolean;
  warnings: string[];
}

export interface FileDeleteResult {
  deleted: { path: string; name: string; is_dir: boolean; size: number }[];
  failed: { path: string; error: string }[];
  items_removed: number;
  links_removed: number;
}

export interface FileRenameResult {
  from: string;
  to: string;
  name: string;
  new_name: string;
  is_dir: boolean;
  size: number;
  items_updated: number;
  links_updated: number;
}

export interface FileMoveResult {
  destination: string;
  moved: { from: string; to: string; name: string; is_dir: boolean; size: number }[];
  skipped: string[];
  failed: { path: string; error: string }[];
  items_updated: number;
  links_updated: number;
}

export interface Work {
  key: string;
  tmdb_id: number;
  media_type: "movie" | "tv";
  title: string;
  original_title?: string;
  year?: number | null;
  overview?: string;
  poster_url?: string;
  backdrop_url?: string;
  logo_url?: string;
  vote_average?: number;
  genres?: string[];
  counts: Record<string, number>;
  seasons: number[];
  season_details?: WorkSeason[];
  items: {
    id: number;
    file_name: string;
    planned_name?: string;
    status: string;
    season?: number | null;
    episode?: number | null;
    target_path?: string | null;
  }[];
}

export interface TaskStats {
  found?: number;
  matched?: number;
  review?: number;
  unmatched?: number;
  ignored?: number;
  missing?: number;
  success?: number;
  failed?: number;
  skipped?: number;
  [key: string]: number | undefined;
}

export interface Task {
  id: number;
  library_id: number | null;
  library_name: string;
  type: "scan" | "organize" | string;
  status: "queued" | "running" | "success" | "partial" | "failed" | "cancelled";
  progress: number;
  total: number;
  done: number;
  stats: TaskStats;
  error?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  logs?: { id: number; ts: string; level: string; message: string }[];
  payload?: Record<string, unknown>;
}

export interface Source {
  id: number;
  name: string;
  type: "local" | "webdav" | "smb";
  config: Record<string, string>;
  has_password: boolean;
  created_at: string;
  libraries?: { id: number; name: string }[];
}

export interface Overview {
  totals: {
    libraries: number;
    items: number;
    works: number;
    matched: number;
    review: number;
    unmatched: number;
    organized: number;
    failed: number;
    pending: number;
  };
  libraries: Library[];
  active_tasks: Task[];
  recent_tasks: Task[];
  pending_items: MediaItem[];
  demo: boolean;
}

export interface SettingsPayload {
  tmdb_api_key_set: boolean;
  tmdb_language: string;
  tmdb_qps: number;
  auto_threshold: number;
  review_threshold: number;
  min_size_mb: number;
  max_files_per_scan: number;
  allow_iso: boolean;
  ai_enabled: boolean;
  ai_base_url: string;
  ai_api_key_set: boolean;
  ai_model: string;
  ai_input_price: number;
  ai_cached_input_price: number;
  ai_output_price: number;
  ai_peak_multiplier: number;
  ai_offpeak_multiplier: number;
  ai_peak_windows: string;
  ai_weekend_offpeak: boolean;
  ai_cn_holidays: string;
  ai_cn_holidays_updated_at: string;
  ai_cn_holidays_source: string;
  ai_pricing_mode: string;
  quark_cookie_set: boolean;
  quark_qps: number;
  p115_cookie_set: boolean;
  p115_qps: number;
  p115_auth_mode: "cookie" | "open";
  p115_open_authorized: boolean;
  p115_open_nickname: string;
  p115_open_uid: number;
  p115_open_expires_at: number;
  p115_open_needs_reauth: boolean;
  p115_open_authorized_at: string;
  bangumi_enabled: boolean;
  bangumi_api_base: string;
  mikan_api_base: string;
  bangumi_main_url: string;
  bangumi_mirror_url: string;
  mikan_main_url: string;
  mikan_mirror_url: string;
  version: string;
}

export interface AiUsageTotals {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  uncached_tokens: number;
  cost: number;
}

export interface AiUsageModel extends AiUsageTotals {
  model: string;
}

export interface AiUsageRecord {
  id: number;
  created_at: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  uncached_tokens: number;
  cost: number;
  reported_cost: number;
  cost_source: string;
  multiplier: number;
  peak: boolean;
  estimated: boolean;
  context: string;
  file_name: string;
}

export interface AiUsagePayload {
  days: number;
  period: AiUsageTotals;
  today: AiUsageTotals;
  all_time: AiUsageTotals;
  by_model: AiUsageModel[];
  recent: AiUsageRecord[];
  prices: {
    input: number;
    cached: number;
    output: number;
    peak_multiplier: number;
    offpeak_multiplier: number;
    peak_windows: string;
    weekend_offpeak: boolean;
  };
}

export interface AiPricingSyncResult {
  source: string;
  model: string;
  input: number;
  cached: number;
  output: number;
  peak_multiplier: number;
  offpeak_multiplier: number;
  peak_windows: string;
  weekend_offpeak: boolean;
  note: string;
}

export interface TagDictionary {
  edition: string[];
  release_notes: string[];
  group: string[];
}

export interface ScheduleConfig {
  mode: "interval" | "cron" | string;
  interval_minutes: number;
  window_start: string;
  window_end: string;
  cron: string;
  timezone: string;
}

export interface SubscriptionRun {
  id: number;
  subscription_id: number;
  status: string;
  trigger: string;
  progress: number;
  total: number;
  done: number;
  stats: Record<string, number>;
  message: string;
  error: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  logs?: { id: number; ts: string; level: string; message: string }[];
  subscription_name?: string;
}

export interface SubscriptionEntry {
  id: number;
  file_name: string;
  remote_path: string;
  saved_path: string;
  task_id: number | null;
  season: number | null;
  episode: number | null;
  version: number;
  version_label: string;
  status: string;
  item_id: number | null;
  target_path: string;
  error: string;
  created_at: string;
  updated_at: string;
}

export interface Subscription {
  id: number;
  name: string;
  type: "quark" | "rss" | string;
  enabled: boolean;
  library_id: number;
  library_name: string;
  config: Record<string, any>;
  schedule: ScheduleConfig;
  next_run_at: string | null;
  deadline: string;
  auto_organize: boolean;
  replace_versions: boolean;
  notify_channel_ids: number[];
  last_run_at: string | null;
  last_status: string;
  last_message: string;
  entry_counts: Record<string, number>;
  active_run: SubscriptionRun | null;
  created_at: string;
  updated_at: string;
}

export interface SubscriptionPreviewSample {
  fid?: string;
  guid?: string;
  name?: string;
  path?: string;
  title?: string;
  title_parsed?: string;
  link?: string;
  download_url?: string;
  download_kind?: string;
  published_at?: string;
  size?: number;
  season?: number | null;
  episode?: number | null;
  version?: number;
  version_label?: string;
}

export interface SubscriptionPreview {
  type: string;
  total: number;
  matched: number;
  samples: SubscriptionPreviewSample[];
  feeds?: { url: string; title: string; count: number; error: string }[];
  truncated: boolean;
}

export interface CloudFolderList {
  path: string;
  quark_path?: string;
  p115_path?: string;
  entries: { name: string; path: string }[];
}

export interface CloudTreeEntry {
  name: string;
  fid: string;
  path: string;
}

export interface CloudTree {
  fid: string;
  path: string;
  cloud_root: string;
  library_path: string | null;
  inside_library: boolean;
  entries: CloudTreeEntry[];
}

export interface QuarkShareTreeEntry {
  fid: string;
  name: string;
  is_dir: boolean;
  size: number;
  path: string;
}

export interface QuarkShareTree {
  share_id: string;
  fid: string;
  path: string;
  entries: QuarkShareTreeEntry[];
  dirs: number;
  files: number;
  truncated: boolean;
}

export interface NotificationEvent {
  key: string;
  label: string;
}

export interface NotificationField {
  key: string;
  label: string;
  type: "text" | "textarea" | "select" | "bool" | "int" | string;
  required: boolean;
  secret: boolean;
  default: any;
  placeholder: string;
  help: string;
  options: string[];
}

export interface NotificationChannelMeta {
  type: string;
  label: string;
  description: string;
  fields: NotificationField[];
}

export interface NotificationCatalog {
  events: NotificationEvent[];
  channels: NotificationChannelMeta[];
}

export interface NotificationChannel {
  id: number;
  name: string;
  type: string;
  config: Record<string, any>;
  secret_fields_set: string[];
  events: string[];
  enabled: boolean;
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_error: string;
  created_at: string;
  updated_at: string;
}

export interface NotificationLog {
  id: number;
  channel_id: number | null;
  event: string;
  title: string;
  ok: number | boolean;
  error: string;
  created_at: string;
  channel_name?: string;
  channel_type?: string;
}

export interface BangumiFollow {
  id: number;
  bangumi_id: number | null;
  title: string;
  title_original: string;
  poster: string;
  feed_url: string;
  mikan_bangumi_id: number | null;
  subgroup_id: number | null;
  subgroup_name: string;
  weekday: number | null;
  weekday_label: string;
  air_time: string;
  total_episodes: number | null;
  latest_episode: number | null;
  next_episode: number | null;
  next_air_at: string | null;
  status: string;
  last_error: string;
  last_synced_at: string | null;
  subscription_id: number | null;
  subscription_name: string;
  subscription_type: string;
  subscription_enabled: boolean;
  subscription_last_status: string;
  schedule_mode: "interval" | "cron";
  check_interval_minutes: number | null;
  cron: string;
  timezone: string;
  created_at: string;
  updated_at: string;
}

export interface BangumiFollowsPayload {
  items: BangumiFollow[];
  today_weekday: number;
  counts: { following: number; paused: number; completed: number };
}

export interface BangumiCalendarItem {
  bangumi_id: number | null;
  mikan_id: number | null;
  title: string;
  title_original: string;
  poster: string;
  air_date: string;
  weekday: number;
  rating: number;
  summary: string;
  url: string;
  followed: boolean;
  follow_id: number | null;
  follow_status: string;
}

export interface BangumiCalendarDay {
  weekday: number;
  label: string;
  items: BangumiCalendarItem[];
}

export interface BangumiCalendar {
  days: BangumiCalendarDay[];
  fetched_at: string;
  stale: boolean;
  error: string;
  today_weekday: number;
}

export interface MikanSubgroup {
  id: number;
  name: string;
  feed_url: string;
}

export interface MikanDetail {
  mikan_id: number;
  bangumi_id: number | null;
  title: string;
  title_original: string;
  poster: string;
  weekday: number | null;
  weekday_label: string;
  total_episodes: number | null;
  feed_url: string;
  subgroups: MikanSubgroup[];
}

export interface RssPreviewItem {
  title: string;
  link: string;
  size: number;
  published_at: string;
}

export interface RssPreview {
  feed_url: string;
  title: string;
  image: string;
  total: number;
  items: RssPreviewItem[];
  truncated: boolean;
}
