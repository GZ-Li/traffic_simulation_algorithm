# 小范围测试
uv run python run_wuhan_net.py \
--net-file data/wuhan_core.net.xml \
--output-dir results \
--cases case3 \
--generations 200 \
--force-recompute 

# 大范围测试
uv run python run_wuhan_net.py \
--net-file data/wuhan.net.xml \
--output-dir results \
--cases case1 \
--generations 200 \
--force-recompute
