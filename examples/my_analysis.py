# my_analysis.py
from phoq.analysis import task


@task("TAGGER")  # the counter(s) feeding it; bare @task means every counter
def coincidences(batch):
    t_l, t_r = batch.timestamps("ch1"), batch.timestamps("ch2")  # absolute seconds
    print(
        f"batch {batch.seq}: {t_l.size} L, {t_r.size} R, {batch.coincidences('ch1','ch2')} both"
    )
    return {"L rate [cps]": batch.rates["ch1"]}  # a dict becomes live read-outs
