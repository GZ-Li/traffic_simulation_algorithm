"""
演唱会散场疏散场景 - 使用示例
"""
from .runner import (
    run_concert_evacuation_experiment,
    generate_concert_evacuation_persons,
    load_person_template_from_mongo,
    save_evacuation_persons_to_file
)


def example_1_generate_and_preview():
    """
    示例1: 生成person数据并预览（仅生成，不运行仿真）
    """
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # 参数配置
    mongo_uri = "mongodb://your-mongo-host:27017"
    agent_db = "agent_db"
    agent_coll = "agent_coll"
    
    # 从MongoDB读取模板
    template = load_person_template_from_mongo(mongo_uri, agent_db, agent_coll)
    if not template:
        print("无法读取模板")
        return
    
    # 生成1000条person数据（示例）
    persons = generate_concert_evacuation_persons(
        template=template,
        num_persons=1000,
        venue_lat=30.476075,
        venue_lon=114.461183,
        home_distance_km=30,
        label="car",
        seed=42
    )
    
    # 保存到文件并预览
    save_evacuation_persons_to_file(persons, "/path/to/output", "preview_persons.json")
    
    # 打印第一条数据作为示例
    print("\n=== 第一条person数据示例 ===")
    import json
    print(json.dumps(persons[0], indent=2, ensure_ascii=False))
    
    print(f"\n总共生成 {len(persons)} 条person数据")


def example_2_run_full_simulation():
    """
    示例2: 运行完整的演唱会疏散仿真（10000人）
    """
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # 参数配置
    mongo_uri = "mongodb://your-mongo-host:27017"
    map_db = "your_map_db"
    map_coll = "your_map_coll"
    agent_db = "your_agent_db"
    agent_coll = "your_agent_coll"
    
    # 运行实验
    metrics = run_concert_evacuation_experiment(
        mongo_uri=mongo_uri,
        map_db=map_db,
        map_coll=map_coll,
        agent_db=agent_db,
        agent_coll=agent_coll,
        start_step=0,
        total_steps=3600,  # 1小时
        interval=10,  # 10秒采样间隔
        output_dir="/path/to/output",
        num_evacuation_persons=10000,  # 10000个疏散人员
        venue_lat=30.476075,
        venue_lon=114.461183,
        home_distance_km=30,
        label="car",
        seed=42,
        output_sql_dsn="",
        output_job_prefix="concert_evacuation_"
    )
    
    print("\n=== 实验完成 ===")
    print(f"最终指标: {metrics}")


if __name__ == "__main__":
    # 运行示例
    # example_1_generate_and_preview()
    example_2_run_full_simulation()
