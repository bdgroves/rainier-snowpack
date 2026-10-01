# snowpack_stats.R
# Mt. Rainier snowpack — season summary and static plots
# Reads data/processed/snotel_daily.csv (written by fetch_snotel.py).
#
# basin_daily.csv now comes from fetch_snotel.py (it carries the Rainier index
# and medians); this script no longer overwrites it.
# The plots go to outputs/, which is not committed — the dashboard draws its
# own charts. Run `pixi run analyze` locally to get them.
# ─────────────────────────────────────────────

library(ggplot2)
library(dplyr)

df <- read.csv("data/processed/snotel_daily.csv", stringsAsFactors = FALSE)
df$date <- as.Date(df$date)
wy <- as.integer(format(max(df$date), "%Y")) + ifelse(as.integer(format(max(df$date), "%m")) >= 10, 1, 0)
wy_origin <- as.Date(sprintf("%d-10-01", wy - 1))

cat("Loaded", nrow(df), "rows |", length(unique(df$station_name)), "stations | WY", wy, "\n")
cat("Date range:", as.character(min(df$date)), "→", as.character(max(df$date)), "\n\n")

# ── Rainier index by day: Σ SWE / Σ median over stations reporting both ──
rainier <- df %>%
  filter(group == "rainier", !is.na(swe_in), !is.na(median_swe_in)) %>%
  group_by(date) %>%
  summarise(swe = mean(swe_in), median = mean(median_swe_in),
            pct = ifelse(sum(median_swe_in) >= n(), 100 * sum(swe_in) / sum(median_swe_in), NA),
            n = n(), .groups = "drop") %>%
  mutate(wy_day = as.integer(date - wy_origin) + 1)

latest <- df %>%
  filter(!is.na(swe_in)) %>%
  group_by(station_name) %>% filter(date == max(date)) %>% ungroup() %>%
  mutate(pct_median = ifelse(!is.na(median_swe_in) & median_swe_in >= 1,
                             round(100 * swe_in / median_swe_in), NA)) %>%
  arrange(desc(elevation_ft))

cat("=== Latest by station ===\n")
print(as.data.frame(latest[, c("station_name", "group", "elevation_ft", "date", "swe_in",
                               "median_swe_in", "pct_median", "depth_in", "temp_f")]))

last <- tail(rainier, 1)
if (nrow(last)) {
  cat("\n=== Rainier index ===\n")
  cat("Mean SWE:", round(last$swe, 1), "in vs median", round(last$median, 1), "in",
      if (!is.na(last$pct)) paste0("(", round(last$pct), "% of median)") else "(too early for % of median)",
      "|", last$n, "stations\n")
}

dir.create("data/processed", showWarnings = FALSE, recursive = TRUE)
dir.create("outputs", showWarnings = FALSE, recursive = TRUE)
write.csv(latest, "data/processed/station_latest_stats.csv", row.names = FALSE)

theme_dark_navy <- theme_minimal() + theme(
  plot.background  = element_rect(fill = "#0a1628", color = NA),
  panel.background = element_rect(fill = "#0a1628", color = NA),
  panel.grid       = element_line(color = "#1e3a5f"),
  text             = element_text(color = "#cdd6f4"),
  axis.text        = element_text(color = "#6b7db3"),
  plot.title       = element_text(color = "white", size = 14, face = "bold"),
  plot.subtitle    = element_text(color = "#4fc3f7", size = 9),
  plot.caption     = element_text(color = "#6b7db3", size = 7),
  legend.background = element_rect(fill = "#0a1628", color = NA),
  legend.key        = element_rect(fill = "#0a1628", color = NA),
  legend.text       = element_text(color = "#cdd6f4", size = 8))

if (nrow(rainier) > 1) {
  p1 <- ggplot(rainier, aes(x = date)) +
    geom_line(aes(y = median), color = "#6b7db3", linetype = "dashed") +
    geom_area(aes(y = swe), fill = "#4fc3f7", alpha = 0.2) +
    geom_line(aes(y = swe), color = "#81d4fa", linewidth = 1) +
    scale_x_date(date_labels = "%b %d") +
    labs(title = "Mt. Rainier — Snow Water Equivalent vs median",
         subtitle = paste0("Mean of ", max(rainier$n), " Rainier SNOTEL stations · WY", wy,
                           " · dashed = NRCS 1991–2020 median"),
         x = NULL, y = "SWE (in)", caption = "Source: NRCS SNOTEL AWDB") +
    theme_dark_navy
  ggsave("outputs/basin_swe_timeseries.png", p1, width = 10, height = 5, dpi = 120, bg = "#0a1628")
}

if (any(!is.na(df$swe_in))) {
p3 <- df %>% filter(!is.na(swe_in)) %>%
  ggplot(aes(x = date, y = swe_in, color = reorder(station_name, -elevation_ft), group = station_name)) +
  geom_line(linewidth = 0.8, alpha = 0.9) +
  scale_x_date(date_labels = "%b %d") +
  labs(title = "Snow Water Equivalent — all stations", subtitle = paste0("WY", wy),
       x = NULL, y = "SWE (in)", color = "Station", caption = "Source: NRCS SNOTEL AWDB") +
  theme_dark_navy
ggsave("outputs/all_stations_swe.png", p3, width = 10, height = 5, dpi = 120, bg = "#0a1628")
}

cat("\n=== snowpack_stats.R complete ===\n")
