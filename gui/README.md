# GUI — 不属于已安装的包

`fimsens_gui.py` 是一个独立的 PyQt6 桌面程序，**不会**被 `pip install fimsens` 装进去。
它是仓库里的附属工具，不是包的一部分。

## 运行

```bash
pip install fimsens[gui]        # 只是把 PyQt6 装上
python gui/fimsens_gui.py
```

## 当前状态：暂停维护

2026-08-18 起搁置，优先做 Python 包本身。**恢复维护前必须先修**：

包在 2026-08-18 调整了两个函数的参数顺序，而本文件是用**位置参数**调用它们的，
现在已经失效（约 1010 行和 1035 行）：

```python
generate_lwse_layers(input_image, dem_path, output_boundary_path, output_centroid_path,
                     n_points=200, min_pixels=20, k_neighbors=5)
# 输出路径移到了三个调参参数之前

idw2Flood1(fcName, demName, floodSeedRaster, idw_temp, outRaster, outClassRaster,
           output_shapefile, alignedSeedRaster,
           fieldName='LWSE_new', max_points=12, power=3.0, flood_class=1)
# fieldName 从第 2 位移到了末尾
```

修法：**把这两处（以及其余所有 fimsens 调用）改成关键字参数**。
包里已经加了守卫，跑起来会明确报"参数顺序在 2026-08-18 变了"，不会静默算错。

完整待办见仓库根目录的 `BACKLOG.md`。
