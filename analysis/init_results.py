#!/usr/bin/env python3
"""Create required header-only outputs once. Never invent or overwrite observations."""
from __future__ import annotations
import argparse, csv, json
from pathlib import Path

SCHEMAS={
"hardware_manifest.csv":"run_id,role,hostname,model,board_revision,cpu_model,cores,threads,ram_bytes,os,kernel,runtime,git_commit,config_hashes,dataset_manifest_hash,seed,start_time,end_time,command_line,temperature_c,throttled,available_memory_bytes,storage_total_bytes,storage_free_bytes",
"network_manifest.csv":"run_id,role,peer,interface,link_speed,duplex,rtt_median_ms,rtt_p95_ms,packet_loss_percent,captured_at_utc",
"dataset_manifest.csv":"study,record_id,owner,subject,activity,interval_id,partition,raw_start_row,raw_end_row,raw_start_timestamp,raw_end_timestamp,purity",
"study1_operation_trace.csv":"run_id,configuration,round_id,role,operation,semantically_necessary_learning,timestamp_utc",
"study1_correctness.csv":"run_id,seed,configuration,round_id,max_absolute_gradient_error,relative_l2_gradient_error,model_parameter_deviation,training_loss_difference,final_rmse_difference,gate_pass",
"study1_privacy.csv":"run_id,seed,configuration,R,equations,rank,singular_values,condition_number,solver,regularization,rank_tolerance,linkage,heart_rate_mae,heart_rate_rmse,heart_rate_r2,feature_standardized_rmse,feature_relative_l2,feature_cosine,activity_balanced_accuracy,activity_macro_f1,reference_accuracy,shuffled_balanced_accuracy,shuffled_macro_f1,metadata_only_balanced_accuracy,metadata_only_macro_f1,attack_restart",
"study1_linkage.csv":"run_id,seed,R,linkage,correct_linkage_fraction,equations,rank,condition_number,heart_rate_mae,feature_standardized_rmse,activity_balanced_accuracy",
"study1_resources.csv":"run_id,repetition,configuration,role,encryption_setup_ns,owner_active_cpu_ns,owner_peak_rss_mib,upload_payload_bytes,upload_serialized_bytes,download_payload_bytes,download_serialized_bytes,sent_bytes,received_bytes,persistent_state_bytes,cold_start_ns,per_round_online_ns,end_to_end_round_ns,temperature_c,throttled,governor,available_memory_bytes,rtt_median_ms,direct_energy_joules,gate_pass",
"study2_utility.csv":"run_id,seed,configuration,round_id,test_balanced_accuracy,test_macro_f1,test_cross_entropy",
"study2_privacy.csv":"run_id,seed,configuration,round_id,view,secret,selected_C,balanced_accuracy,macro_f1,confusion_matrix,class_support,majority_balanced_accuracy,majority_macro_f1,shuffled_balanced_accuracy,shuffled_macro_f1,attack_restart",
"study2_cut_comparison.csv":"seed,configuration,activity_balanced_accuracy,subject_balanced_accuracy,test_balanced_accuracy,test_macro_f1,test_cross_entropy,owner_active_cpu_ns,owner_peak_rss_mib,bytes_sent,bytes_received,persistent_state_bytes,round_wall_ns",
"study2_hmac_controls.csv":"run_id,seed,control,expected_accept,observed_accept,reason,hmac_compute_ns,key_bytes,tag_bytes,replay_state_bytes,gate_pass",
"study2_work_evasion.csv":"run_id,seed,manipulation,fraction,hmac_acceptance,executor_active_compute_ns,executor_compute_saved_percent,l2_distance,cosine_similarity,round_id,global_loss,test_balanced_accuracy,test_macro_f1,convergence_effect,attack_restart",
"study2_resources.csv":"run_id,repetition,configuration,role,owner_active_cpu_ns,owner_protocol_online_ns,end_to_end_round_ns,peak_rss_mib,payload_bytes_sent,serialized_bytes_sent,socket_bytes_sent,payload_bytes_received,serialized_bytes_received,socket_bytes_received,persistent_state_bytes,cold_start_ns,temperature_c,throttled,governor,available_memory_bytes,rtt_median_ms,direct_energy_joules,gate_pass",
}

CKKS={"status":"pilot_candidate","lattigo":{"module":"github.com/tuneinsight/lattigo/v5","version":"v5.0.7","git_commit":"03e551357ce0ae62276727347584040f42939563"},"go":{"version":"go1.26.5","build_flags":["-trimpath","-ldflags=-buildid="]},"scheme":"CKKS","logN":14,"polynomial_modulus_degree":16384,"logQ":[55,45,45,45,45],"logP":[61],"log_default_scale":45,"levels":4,"maximum_multiplicative_depth":4,"required_depth":3,"rotations":[],"packing_layout":"one real scalar in slot 0 per ciphertext; 12 feature ciphertexts and one target ciphertext per record","relinearization_keys":1,"rotation_keys":0,"ciphertext_count_formula":"13*N encrypted input; I-OR final 13; I-ER residual N plus final 13","ciphertext_serialization":"Lattigo MarshalBinary inside uint32-length list","estimated_security_bits":None,"pilot_evidence":None,"frozen_at_utc":None}

def main():
    p=argparse.ArgumentParser();p.add_argument("--root",type=Path,required=True);a=p.parse_args();out=a.root/"results"/"processed";out.mkdir(parents=True,exist_ok=True)
    for name,schema in SCHEMAS.items():
        path=out/name
        if path.exists():continue
        with path.open("x",newline="",encoding="utf-8") as f:csv.writer(f).writerow(schema.split(","))
    ckks=out/"study1_ckks_parameters.json"
    if not ckks.exists():
        with ckks.open("x",encoding="utf-8") as f:json.dump(CKKS,f,sort_keys=True,indent=2);f.write("\n")
if __name__=="__main__":main()
