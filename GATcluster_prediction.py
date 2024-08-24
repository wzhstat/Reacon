import pandas as pd
import csv
import json,gzip
import argparse
from rdkit import Chem
from rdkit.Chem.Fingerprints import FingerprintMols
from rdkit import DataStructs
from joblib import Parallel, delayed
import time
import os
from tqdm import tqdm
from GAT.GAT_models import GAT
from GAT.get_dataset import GraphDataset, collate_fn, GraphDataLoader
import torch

def make_predictions(test_path,num_classes,model_weights_path, target = None):
    if torch.cuda.is_available():
        torch.cuda.set_device(2)
        device = torch.device('cuda')
    else:
        device = torch.device('cpu')
    data_test = pd.read_csv(test_path)
    node_in_channels = 165
    edge_in_channels = 193
    hidden_channels = 1024
    num_classes = num_classes
    num_layers = 5
    num_heads = 8
    dropout =0.2
    model = GAT(node_in_channels, num_classes, num_layers, num_heads, dropout, edge_in_channels)
    model.load_state_dict(torch.load("%s.pth"%model_weights_path))
    test_dataset = GraphDataset(data_test,target)
    test_loader = GraphDataLoader(test_dataset, batch_size=1, collate_fn=collate_fn)
    pred = []
    model.eval()
    model.to(device)
    with torch.no_grad():
        for batch in tqdm(test_loader, desc="Processing batches"):
            batch = batch.to(device)
            outputs = model(batch)
            outputs = torch.softmax(outputs, dim=1)
            outputs.to(torch.device('cpu'))
            pred.append(outputs)
    return pred

class condition_candidate():
    def __init__(self):
        self.rxn_smart = str()
        self.class_id = int()
        self.class_label = list()
        self.conditions = list()
        self.condition_score = dict()
        self.temp_similarity = float()
        self.max_score = float()
        self.max_score_condition = list()
    
    def get_class_id(self,class_id):
        self.class_id = class_id
    
    def get_class_label(self,class_label):
        self.class_label = class_label
    
    def get_rxn_smart(self,rxn_smart):
        self.rxn_smart = rxn_smart
    
    def get_conditions(self,conditions):
        self.conditions = conditions
    
    def get_temp_similarity(self,temp_similarity):
        self.temp_similarity = temp_similarity
    
    def cal_condition_score(self,condition,MPNN_out):
        score = self.temp_similarity
        for i in range(len(condition)-1):
            try:
                score *= MPNN_out[i][condition[i]]
            except:
                score *= 1e-10
        return float(score)
    
    def get_condition_score(self,MPNN_out,condition_key):
        cat_list,solv_list,reag_list = condition_key
        for condition in self.conditions:
            text_condition = decode_condition([condition],cat_list,solv_list,reag_list)
            self.condition_score[str(text_condition[0])] = self.cal_condition_score(condition,MPNN_out)
        self.condition_score = sorted(self.condition_score.items(), key=lambda x:x[1],reverse=True)
    
    def get_max_score(self):
        self.max_score = self.condition_score[0][1]
        self.max_score_condition = self.condition_score[0][0]
            
def cal_temp_similarity(tem1,tem2):
    '''
    calculate template similarity
    args:
        tem1: template1
        tem2: template2
    '''
    if tem1 == 'None' or tem2 == 'None':
        return 0
    tem1 = tem1.split('>>')
    tem2 = tem2.split('>>')
    mol1 = [Chem.MolFromSmarts(s) for s in tem1]
    mol2 = [Chem.MolFromSmarts(s) for s in tem2]
    fps1 = [FingerprintMols.FingerprintMol(m) for m in mol1]
    fps2 = [FingerprintMols.FingerprintMol(m) for m in mol2]
    score = 1
    for i in range(len(fps1)):
        score *= DataStructs.FingerprintSimilarity(fps1[i],fps2[i])
    return score

def encode_condition(condition_list:list,cat_list:list,solv_list:list,reag_list:list):
    '''
    encode condition list to index
    args:
        condition_list: condition list
        cat_list: catalyst list
        solv_list: solvent list
        reag_list: reagent list
    '''
    out = []
    for condition in condition_list:
        cat = cat_list.index(condition[0])
        solv1 = solv_list.index(condition[1])
        solv2 = solv_list.index(condition[2])
        reag1 = reag_list.index(condition[3])
        reag2 = reag_list.index(condition[4])
        reag3 = reag_list.index(condition[5])
        out.append([cat,solv1,solv2,reag1,reag2,reag3])
    return out

def decode_condition(condition_list:list,cat_list:list,solv_list:list,reag_list:list):
    '''
    decode condition list to index
    args:
        condition_list: condition list
        cat_list: catalyst list
        solv_list: solvent list
        reag_list: reagent list
    '''
    out = []
    for condition in condition_list:
        cat = cat_list[condition[0]]
        solv1 = solv_list[condition[1]]
        solv2 = solv_list[condition[2]]
        reag1 = reag_list[condition[3]]
        reag2 = reag_list[condition[4]]
        reag3 = reag_list[condition[5]]
        out.append([cat,solv1,solv2,reag1,reag2,reag3])
    return out


def get_condition_key(path:str):
    with open('%s/cat_labels.csv'%path,'r') as f:
        reader = csv.DictReader(f)
        cat_list_N = [row['cat'] for row in reader]

    with open('%s/solv_labels.csv'%path,'r') as f:
        reader = csv.DictReader(f)
        solv_list_N = [row['solv'] for row in reader]

    with open('%s/reag_labels.csv'%path,'r') as f:
        reader = csv.DictReader(f)
        reag_list_N = [row['reag'] for row in reader]   
    
    return cat_list_N,solv_list_N,reag_list_N


def get_condition_score(candidate_range,pred,condition_key):
    if len(candidate_range) == 0:
        return []
    condition_pre = [] 
    for candidate in candidate_range:
        candidate.get_condition_score(pred,condition_key)
        candidate.get_max_score()
        condition_pre.append({'class_id':candidate.class_id,'class_label':candidate.class_label,'best condition':eval(candidate.max_score_condition) ,'score':candidate.max_score,'condition_score':candidate.condition_score})
    condition_pre = sorted(condition_pre, key=lambda x:x['score'],reverse=True)
    return condition_pre

def merge_same_lable_candidate(candidate_range):
    label_list = []
    out = []
    for candidate in candidate_range:
        class_label = candidate.class_label[0]+candidate.class_label[1]
        if set(class_label) not in label_list:
            label_list.append(set(class_label))
            out.append(candidate)
        else:
            for i in out:
                if set(i.class_label[0]+i.class_label[1]) == set(class_label):
                    i.conditions += candidate.conditions
    return out

def get_candidate_range(args,target_temp,classed_conditions_library):
        candidate_range = []
        for condition_class in classed_conditions_library[target_temp]:
            '''
            if condition_class['class_label'] == [[], []]:
                continue
            else:
            '''
            candidate = condition_candidate()
            candidate.get_class_id(condition_class['class_id'])
            candidate.get_temp_similarity(1)
            candidate.get_class_label(condition_class['class_label'])
            candidate.get_conditions(condition_class['encoded_conditions'])
            candidate_range.append(candidate)
        return candidate_range



def condition_selector(args, temp:str,pred:list,classed_conditions_library:dict):
    try:
        candidate_range = get_candidate_range(args,temp,classed_conditions_library)
        candidate_range = get_condition_score(candidate_range,pred,condition_key)
        return candidate_range
    except:
        return []


def Prediction(args):
    '''
    This function is used to predict the reaction conditions of the test data.
    args:
        args.test_path: path to test data
        args.model_path: path to model
        args.label_path: path to labels
        args.library_path: path to classed conditions library
        args.save_path: path to save condition prediction
    '''
    global condition_key
    t1 = time.time()
    # Load data
    test_data = pd.read_csv(args.test_path)
    ids = test_data['_id']
    smiles = [[test_data['reaction'][i]] for i in range(len(test_data))]
    template_r0 = test_data['tpl_SMARTS_r0']
    template_r1 = test_data['tpl_SMARTS_r1']
    template_r_1 = test_data['tpl_SMARTS_r0*']
    # MPNN prediction
    MPNN_pred = {}
    num_classes_dic = {"cat":439, "solv0":542, "solv1":542, "reag0":2746, "reag1":2746, "reag2":2746 }
    for target in ['cat','solv0','solv1','reag0','reag1','reag2']:
        model_dir = "%s/model_%s"%(args.model_path,target)
        MPNN_pred[target] = make_predictions(args.test_path,num_classes_dic[target],model_dir)
    
    t2 = time.time()
    print('time:',t2-t1)
    
    # Load condition key
    condition_key = get_condition_key(args.label_path)

    # Load classed_conditions_library
    with gzip.open(args.library_path+'/classed_conditions_library_r0_1.json.gz','r') as f:
        classed_conditions_library_r_1 = json.load(f)
    with gzip.open(args.library_path+'/classed_conditions_library_r0.json.gz','r') as f:
        classed_conditions_library_r0 = json.load(f)
    with gzip.open(args.library_path+'/classed_conditions_library_r1.json.gz','r') as f:
        classed_conditions_library_r1 = json.load(f)
    
    # Get condition clusters prediction
    #print(len(list(MPNN_pred['cat'][0][0])),len(list(MPNN_pred['solv0'][0][0])),len(list(MPNN_pred['solv1'][0][0])),len(list(MPNN_pred['reag0'][0][0])),len(list(MPNN_pred['reag1'][0][0])),len(list(MPNN_pred['reag2'][0][0])))
    condition_pred = {}
    for i in range(test_data.shape[0]):
        if template_r1[i] in classed_conditions_library_r1:
            condition_pred[str(ids[i])] = ('r1:',condition_selector(args,template_r1[i],[list(MPNN_pred['cat'][i])[0],list(MPNN_pred['solv0'][i])[0],list(MPNN_pred['solv1'][i])[0],list(MPNN_pred['reag0'][i])[0],list(MPNN_pred['reag1'][i])[0],list(MPNN_pred['reag2'][i])[0]],classed_conditions_library_r1))
        elif template_r0[i] in classed_conditions_library_r0:
            condition_pred[str(ids[i])] = ('r0:',condition_selector(args,template_r0[i],[list(MPNN_pred['cat'][i])[0],list(MPNN_pred['solv0'][i])[0],list(MPNN_pred['solv1'][i])[0],list(MPNN_pred['reag0'][i])[0],list(MPNN_pred['reag1'][i])[0],list(MPNN_pred['reag2'][i])[0]],classed_conditions_library_r0))
        else:
            condition_pred[str(ids[i])] = ('r0*:',condition_selector(args,template_r_1[i],[list(MPNN_pred['cat'][i])[0],list(MPNN_pred['solv0'][i])[0],list(MPNN_pred['solv1'][i])[0],list(MPNN_pred['reag0'][i])[0],list(MPNN_pred['reag1'][i])[0],list(MPNN_pred['reag2'][i])[0]],classed_conditions_library_r_1))
    # Save
    if not os.path.exists(os.path.dirname(args.save_path)):
        os.makedirs(os.path.dirname(args.save_path))
    with open('%s/cluster_condition_prediction.json'%args.save_path,'w') as f:
        json.dump(condition_pred,f)
    t3 = time.time()
    print('time:',t3-t1)
    print('save to: %s'%args.save_path)
    print('done')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='reaction condition prediction')
    parser.add_argument('--test_path', type=str, default='./data/data_test.csv', help='path to test data')
    parser.add_argument('--model_path', type=str, default='./GATmodels', help='path to model')
    parser.add_argument('--label_path', type=str, default='./data/labels', help='path to condition labels')
    parser.add_argument('--library_path', type=str, default='./data/condition_library', help='path to classed conditions library')
    parser.add_argument('--save_path', type=str, default='./data/GATprediction', help='path to save condition prediction')
    args = parser.parse_args()
    Prediction(args)
