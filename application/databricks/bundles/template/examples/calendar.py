# Databricks notebook source
# MAGIC %run ../common

# COMMAND ----------

from oeds_workflow.utils.calendar.calendar_utils import CalendarUtils, Weekday

# COMMAND ----------

from datetime import date

holidays = [
    date(2026, 1, 1),
    date(2026, 4, 3),
    date(2026, 5, 18),
    date(2026, 7, 1),
    date(2026, 12, 25),
]

du = CalendarUtils.from_holidays(holidays)
d = date(2026, 4, 12)

print("today:", du.today())

# COMMAND ----------

print("day_add:", du.day_add(d, 3))
print("day_prev:", du.day_prev(d))
print("day_next:", du.day_next(d))

# COMMAND ----------

print("weekday_add:", du.weekday_add(d, 2))
print("weekday_prev:", du.weekday_prev(d))
print("weekday_next:", du.weekday_next(d))
print("weekday_this Tuesday:", du.weekday_this(d, Weekday.TUESDAY))
print("weekday_prev_occurrence Monday:", du.weekday_prev_occurrence(d, Weekday.MONDAY))
print("weekday_next_occurrence Friday:", du.weekday_next_occurrence(d, Weekday.FRIDAY))
print("week_monday:", du.week_monday(d))

# COMMAND ----------

print("business_day_prev:", du.business_day_prev(d))
print("business_day_next:", du.business_day_next(d))
print("business_day_add +2:", du.business_day_add(d, 2))

# COMMAND ----------

print("is_business_day:", du.is_business_day(d))
print("is_holiday:", du.is_holiday(d))
print("is_weekday:", du.is_weekday(d))
print("is_weekend:", du.is_weekend(d))
print("is_day_of_week Sunday:", du.is_day_of_week(d, Weekday.SUNDAY))
print("is_monday:", du.is_monday(d))
print("is_tuesday:", du.is_tuesday(d))
print("is_wednesday:", du.is_wednesday(d))
print("is_thursday:", du.is_thursday(d))
print("is_friday:", du.is_friday(d))
print("is_saturday:", du.is_saturday(d))
print("is_sunday:", du.is_sunday(d))

# COMMAND ----------

print("month_first_day:", du.month_first_day(d))
print("month_last_day:", du.month_last_day(d))
print("month_nth_weekday second Tuesday:", du.month_nth_weekday(d, Weekday.TUESDAY, 2))
print("month_first_weekday Monday:", du.month_first_weekday(d, Weekday.MONDAY))
print("month_second_weekday Tuesday:", du.month_second_weekday(d, Weekday.TUESDAY))
print("month_third_weekday Friday:", du.month_third_weekday(d, Weekday.FRIDAY))
print("month_fourth_weekday Sunday:", du.month_fourth_weekday(d, Weekday.SUNDAY))
print("month_last_weekday Friday:", du.month_last_weekday(d, Weekday.FRIDAY))
