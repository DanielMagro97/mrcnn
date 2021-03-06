# Import standard modules
import os
import sys
import json
import time
import datetime
import logging
import numpy as np


# Import Mask RCNN

from mrcnn.config import Config
from mrcnn import model as modellib, utils
from mrcnn import visualize
from mrcnn.graph import Graph


# Import image modules
import skimage.draw
import skimage.measure
from skimage.measure import find_contours


## Import graphics modules
import matplotlib
#matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import patches, lines
from matplotlib.patches import Polygon

## Get logger
logger = logging.getLogger(__name__)


# ========================
# ==    MODEL TESTER
# ========================
class ModelTester(object):
	""" Define analyzer object """

	def __init__(self,model,config,dataset):
		""" Return an analyzer object """

		self.dataset= dataset
		self.model= model
		self.config= config

		# - Data options
		self.n_max_img= -1

		# - Process options
		self.score_thr= 0.7
		self.iou_thr= 0.6

		# - Results
		self.n_classes= self.config.NUM_CLASSES
		self.classification_matrix= np.zeros((self.n_classes,self.n_classes))
		self.classification_matrix_norm= np.zeros((self.n_classes,self.n_classes))
		self.purity= np.zeros((1,self.n_classes))
		self.nobjs_true= np.zeros((1,self.n_classes))
		self.nobjs_det= np.zeros((1,self.n_classes))
		self.nobjs_det_right= np.zeros((1,self.n_classes))
	
	# ========================
	# ==     TEST
	# ========================
	def test(self):
		""" Test the model on input dataset """    
	
		# - Loop over dataset and inspect results
		nimg= 0
		logger.info("Processing up to %d images " % (self.n_max_img))

		for index, image_id in enumerate(self.dataset.image_ids):
			nimg+= 1
		
			# - Check if stop inspection
			if self.n_max_img>0 and nimg>=self.n_max_img:
				logger.info("Max number of images to inspect reached, stop here.")
				break

			# - Inspect results	for current image
			image_path = self.dataset.image_info[index]['path']
			image_path_base= os.path.basename(image_path)

			# - Initialize the analyzer
			analyzer= Analyzer(self.model,self.config,self.dataset)
			analyzer.score_thr= self.score_thr
			analyzer.iou_thr= self.iou_thr
			
			# - Inspecting results
			logger.info("Inspecting results for image %s ..." % image_path_base)
			status= analyzer.inspect_results(image_id,image_path)
			if status<0:
				logger.error("Failed to analyze results for image %s ..." % image_path_base)
				continue

			# - Update performances
			logger.info("Updating test performances using results for image %s ..." % image_path_base)
			self.update_performances(analyzer)
			
		# - Compute final results
		logger.info("Computing final performances ...")
		self.compute_performances()

		return 0

	# =============================
	# ==     UPDATE PERFORMANCES
	# =============================
	def update_performances(self,analyzer):
		""" Update test performances using current sample """
		
		# - Retrieve perf data for sample image
		C_sample= analyzer.confusion_matrix
		nobjs_true_sample= analyzer.nobjs_true
		nobjs_det_sample= analyzer.nobjs_det
		nobjs_det_right_sample= analyzer.nobjs_det_right

		# - Sum perf data
		self.classification_matrix+= C_sample
		self.nobjs_true+= nobjs_true_sample
		self.nobjs_det+= nobjs_det_sample
		self.nobjs_det_right+= nobjs_det_right_sample

	# =============================
	# ==     COMPUTE PERFORMANCES
	# =============================
	def compute_performances(self):
		""" Compute final performances """
		
		# - Normalize classification matrix
		for i in range(self.n_classes):
			norm= self.nobjs_true[0][i]
			if norm<=0:
				continue
			for j in range(self.n_classes):
				C= self.classification_matrix[i][j]
				C_norm= C/norm
				self.classification_matrix_norm[i][j]= C_norm

		# - Compute purity
		for j in range(self.n_classes):
			if self.nobjs_det[0][j]<=0:
				continue
			p= self.nobjs_det_right[0][j]/self.nobjs_det[0][j]
			self.purity[0][j]= p

		# - Print results
		print("== NOBJ TRUE ==")
		print(self.nobjs_true)

		print("== NOBJ DET ==")
		print(self.nobjs_det)

		print("== NOBJ DET CORRECTLY ==")
		print(self.nobjs_det_right)

		print("== CLASSIFICATION MATRIX ==")
		print(self.classification_matrix)

		print("== CLASSIFICATION MATRIX (NORM) ==")
		print(self.classification_matrix_norm)

		print("== PURITY ==")
		print(self.purity)

# ========================
# ==    ANALYZER
# ========================
class Analyzer(object):
	""" Define analyzer object """

	def __init__(self,model,config,dataset=None):
		""" Return an analyzer object """

		# - Model
		self.model= model
		self.r= None

		# - Config options
		self.config= config
		self.n_classes= self.config.NUM_CLASSES

		# - Data options
		self.dataset= dataset
		self.image= None
		self.image_id= -1
		self.image_path= ''
		self.image_path_base= ''
		self.image_path_base_noext= ''
		
		# - Raw model data
		self.class_names= None
		self.masks= None
		self.boxes= None
		self.class_ids= None
		self.scores= None
		self.nobjects= 0

		# - Processed ground truth masks
		self.masks_gt_merged= []
		self.class_ids_gt_merged= []
		self.bboxes_gt= []
		self.captions_gt= []

		# - Processed detected masks
		self.masks_final= []
		self.class_ids_final= []
		self.scores_final= []	
		self.bboxes= []
		self.captions= []

		# - Process options
		self.score_thr= 0.7
		self.iou_thr= 0.6

		# - Performances results
		self.confusion_matrix= np.zeros((self.n_classes,self.n_classes))
		self.confusion_matrix_norm= np.zeros((self.n_classes,self.n_classes))	
		self.purity= np.zeros((1,self.n_classes))
		self.nobjs_true= np.zeros((1,self.n_classes))
		self.nobjs_det= np.zeros((1,self.n_classes))
		self.nobjs_det_right= np.zeros((1,self.n_classes))

		# - Draw options
		self.draw= True
		self.class_color_map= {
			'bkg': (0,0,0),# black
			'sidelobe': (1,1,0),# yellow
			'source': (1,0,0),# red
			'galaxy_C2': (0,0,1),# blue
			'galaxy_C3': (0,1,0),# green
		}

	def set_image_path(self,path):
		""" Set image path """
		self.image_path= path
		self.image_path_base= os.path.basename(self.image_path)
		self.image_path_base_noext= os.path.splitext(self.image_path_base)[0]

	# =============================
	# ==     GET DATA FROM MODEL
	# =============================
	def get_data(self):
		""" Retrieve data from dataset & model """

		# - Throw error if dataset is not given
		if not self.dataset:
			logger.error("No dataset present!")
			return -1

		# - Load image
		self.image = self.dataset.load_image(self.image_id)
		self.image_path_base= os.path.basename(self.image_path)
		self.image_path_base_noext= os.path.splitext(self.image_path_base)[0]		

		# - Get detector result
		r = self.model.detect([self.image], verbose=0)[0]
		self.class_names= self.dataset.class_names
		self.masks= r['masks']
		self.boxes= r['rois']
		self.class_ids= r['class_ids']
		self.scores= r['scores']
		self.nobjects= self.masks.shape[-1]
		#N = boxes.shape[0]

		# - Retrieve ground truth masks
		#self.masks_gt= self.dataset.load_gt_mask(image_id)
		self.masks_gt= self.dataset.load_gt_mask_nonbinary(self.image_id)
		self.class_id_gt = self.dataset.image_info[self.image_id]["class_id"]
		self.label_gt= self.class_names[self.class_id_gt]
		self.color_gt = self.class_color_map[self.label_gt]
		self.caption_gt = self.label_gt

		return 0

	# ========================
	# ==     PREDICT
	# ========================
	def predict(self,image,image_id='',bboxes_gt=[]):
		""" Predict results on given image """

		# - Throw error if image is None
		if image is None:
			logger.error("No input image given!")
			return -1
		self.image= image

		if image_id:
			self.image_id= image_id

		# - Get detector result
		r = self.model.detect([self.image], verbose=0)[0]
		self.class_names= self.config.CLASS_NAMES
		self.masks= r['masks']
		self.boxes= r['rois']
		self.class_ids= r['class_ids']
		self.scores= r['scores']
		self.nobjects= self.masks.shape[-1]

		# - Process detected masks
		if self.nobjects>0:
			logger.info("Processing detected masks for image %s ..." % self.image_id)
			self.extract_det_masks()
		else:
			logger.warn("No detected object found for image %s ..." % self.image_id)
			return 0
 
		# - Set gt box if given
		self.bboxes_gt= bboxes_gt
			
		# - Draw results
		if self.draw:
			logger.info("Drawing results for image %s ..." % str(self.image_id))
			outfile =  'out_' + str(self.image_id) + '.png'
			self.draw_results(outfile)
	
		return 0

	# ========================
	# ==     INSPECT
	# ========================
	def inspect_results(self,image_id,image_path):
		""" Inspect results on given image """
	
		# - Retrieve data from dataset & model
		logger.info("Retrieve data from dataset & model ...")
		self.image_id= image_id
		self.image_path= image_path
		if self.get_data()<0:
			logger.error("Failed to set data from provided dataset!")
			return -1

		# - Process ground truth masks
		logger.info("Processing ground truth masks ...")
		self.extract_gt_masks()

		# - Process detected masks
		if self.nobjects>0:
			logger.info("Processing detected masks ...")
			self.extract_det_masks()
		else:
			logger.warn("No detected object found for image %s ..." % self.image_path_base)

		# - Compute performance results
		logger.info("Compute performance results for image %s ..." % self.image_path_base)
		self.compute_performances()

		# - Draw results
		if self.draw:
			logger.info("Drawing results for image %s ..." % self.image_path_base)
			outfile =  'out_' + self.image_path_base_noext + '.png'
			self.draw_results(outfile)

		return 0
		
	# ========================
	# ==   EXTRACT GT MASKS
	# ========================
	def extract_gt_masks(self):
		""" Extract ground truth masks & bbox """

		# - Reset gt data
		self.masks_gt_merged= []
		self.class_ids_gt_merged= []
		self.bboxes_gt= []
		self.captions_gt= []

		# - Inspect ground truth masks
		masks_gt_det= []
		class_ids_gt_det= []

		for k in range(self.masks_gt.shape[-1]):
			mask_gt= self.masks_gt[:,:,k]
			if self.label_gt=='galaxy_C2' or self.label_gt=='galaxy_C3':
				masks_gt_det.append(mask_gt)
				class_ids_gt_det.append(self.class_id_gt)
				continue

			component_labels_gt, ncomponents_gt= self.extract_mask_connected_components(mask_gt)
			logger.debug("Found %d sub components in gt mask no. %d ..." % (ncomponents_gt,k))
		
			#indices = np.indices(mask_gt.shape).T[:,:,[1, 0]]

			for i in range(ncomponents_gt):	
				#mask_indices= indices[component_labels_gt==i+1]
				mask_indices= np.where(component_labels_gt==1)
				extracted_mask= np.zeros(mask_gt.shape,dtype=mask_gt.dtype)
				#extracted_mask[mask_indices[:,0],mask_indices[:,1]]= 1
				#extracted_mask= np.where(component_labels_gt==1, [1], [0])
				extracted_mask[mask_indices]= 1

				# - Extract true object id from gt mask pixel values (1=sidelobes,2=sources,3=...)
				#   Override class_id_gt
				#object_classid= mask_gt[mask_indices[0,0],mask_indices[0,1]]
				object_classid= mask_gt[mask_indices[0][0],mask_indices[1][0]]
				logger.debug("gt mask no. %d (subcomponent no. %d): object_classid=%d ..." % (k,i,object_classid))

				masks_gt_det.append(extracted_mask)
				#class_ids_gt_det.append(self.class_id_gt)
				class_ids_gt_det.append(object_classid)
			
		N= len(masks_gt_det)
		g= Graph(N)
		for i in range(N):
			for j in range(i+1,N):
				connected= self.are_mask_connected(masks_gt_det[i],masks_gt_det[j])
				same_class= (class_ids_gt_det[i]==class_ids_gt_det[j])
				mergeable= (connected and same_class)
				if mergeable:
					logger.debug("GT mask (%d,%d) have connected components and can be merged..." % (i,j))
					g.addEdge(i,j)

		cc = g.connectedComponents()
		print(cc) 

		
		for i in range(len(cc)):
			if not cc[i]:
				continue
		
			n_merged= len(cc[i])

			for j in range(n_merged):
				index= cc[i][j]
				mask= masks_gt_det[index]
				class_id= class_ids_gt_det[index]
			
				logger.debug("Merging GT mask no. %d (class_id=%d) ..." % (index,class_id))
				if j==0:
					merged_mask= mask
					#merged_score= score
				else:
					merged_mask= self.merge_masks(merged_mask,mask)
	
			self.masks_gt_merged.append(merged_mask)
			self.class_ids_gt_merged.append(class_id)
		
		
		for i in range(len(self.masks_gt_merged)):
			mask= self.masks_gt_merged[i]
			height= mask.shape[0]
			width= mask.shape[1]
			mask_expanded = np.zeros([height,width,1],dtype=np.bool)
			mask_expanded[:,:,0]= mask
			bbox= utils.extract_bboxes(mask_expanded)
			self.bboxes_gt.append(bbox[0])
	
			label= self.class_names[self.class_ids_gt_merged[i]]
			caption = label
			self.captions_gt.append(caption)	
		

		
	# ========================
	# ==   EXTRACT DET MASKS
	# ========================
	def extract_det_masks(self):
		""" Extract detected masks & bbox """
		
		# - Reset mask data		
		self.masks_final= []
		self.class_ids_final= []
		self.scores_final= []	
		self.bboxes= []
		self.captions= []

		# - Select detected objects with score larger than threshold
		N = self.boxes.shape[0]
		masks_sel= []
		class_ids_sel= []
		scores_sel= []
		nobjects_sel= 0
		logger.info("%d objects (%d boxes) found in this image ..." % (self.nobjects,N))

		for i in range(N):
			mask= self.masks[:, :, i]
			class_id = self.class_ids[i]
			score = self.scores[i]
			label = self.class_names[class_id]
			caption = "{} {:.3f}".format(label, score)
			if score<self.score_thr:
				logger.info("Skipping object %s (id=%d) with score %f<thr=%f ..." % (label,class_id,score,self.score_thr))
				continue

			logger.info("Selecting object %s (id=%d) with score %f>thr=%f ..." % (label,class_id,score,self.score_thr))
			masks_sel.append(mask)
			class_ids_sel.append(class_id)
			scores_sel.append(score)
			nobjects_sel+= 1
		
		logger.info("%d objects selected in this image ..." % nobjects_sel)

		# - Sort objects by descending scores
		sort_indices= np.argsort(scores_sel)[::-1]

		# - Separate all detected objects which are not connected.
		#   NB: This is done only for sources & sidelobes not for galaxies
		masks_det= []
		class_ids_det= []
		scores_det= []
		nobjects_det= 0

		for index in sort_indices:
			mask= masks_sel[index]	
			class_id= class_ids_sel[index]
			label= self.class_names[class_id]
			score= scores_sel[index]

			# - Skip if class id is galaxy
			if label=='galaxy_C2' or label=='galaxy_C3':
				masks_det.append(mask)
				class_ids_det.append(class_id)
				scores_det.append(score)
				logger.info("Selecting object %s (id=%d) with score %f>thr=%f ..." % (label,class_id,score,self.score_thr))
				continue

			# - Extract components masks
			component_labels, ncomponents= self.extract_mask_connected_components(mask)
			logger.debug("Found %d sub components in mask no. %d ..." % (ncomponents,index))
		
			# - Extract indices of components and create masks for extracted components
			#indices = np.indices(mask.shape).T[:,:,[1, 0]]
			
			#print("DEBUG: component_labels.shape")	
			#print(component_labels.shape)
			#print("DEBUG: mask.shape")
			#print(mask.shape)
			#print("DEBUG: np.indices(mask.shape)")
			#print(np.indices(mask.shape).shape)
			#print("DEBUG: indices")
			#print(indices.shape)
			#print("DEBUG: indices2")
			#print(indices2.shape)			

			for i in range(ncomponents):	
				#mask_indices= indices[component_labels==i+1]
				extracted_mask= np.zeros(mask.shape,dtype=mask.dtype)
				#extracted_mask[mask_indices[:,0],mask_indices[:,1]]= 1
				extracted_mask= np.where(component_labels==i+1, [1], [0])

				masks_det.append(extracted_mask)
				class_ids_det.append(class_id)
				scores_det.append(score)
				logger.info("Selecting object %s (id=%d) with score %f>thr=%f ..." % (label,class_id,score,self.score_thr))
			

		logger.info("Found %d components overall (after non-connected component extraction) in this image ..." % (len(masks_det)))
		
		# - Init undirected graph
		#   Add links between masks that are connected
		N= len(masks_det)
		g= Graph(N)
		for i in range(N):
			for j in range(i+1,N):
				connected= self.are_mask_connected(masks_det[i],masks_det[j])
				same_class= (class_ids_det[i]==class_ids_det[j])
				mergeable= (connected and same_class)
				if mergeable:
					logger.debug("Mask (%d,%d) have connected components and can be merged..." % (i,j))
					g.addEdge(i,j)

		# - Compute connected masks
		cc = g.connectedComponents()
		#print(cc) 

		# - Merge connected masks
		masks_merged= []
		class_ids_merged= []
		scores_merged= []

		for i in range(len(cc)):
			if not cc[i]:
				continue
		
			score_avg= 0
			n_merged= len(cc[i])

			for j in range(n_merged):
				index= cc[i][j]
				mask= masks_det[index]
				class_id= class_ids_det[index]
				score= scores_det[index]
				score_avg+= score

				logger.debug("Merging mask no. %d ..." % index)
				if j==0:
					merged_mask= mask
					merged_score= score
				else:
					merged_mask= self.merge_masks(merged_mask,mask)
	
			score_avg*= 1./n_merged	
			masks_merged.append(merged_mask)
			class_ids_merged.append(class_id)
			scores_merged.append(score_avg)
		
		logger.info("#%d masks found after merging ..." % len(masks_merged))


		# - Find if there are overlapping masks with different class id
		#   If so retain the one with largest score
		N_final= len(masks_merged)
		g_final= Graph(N_final)
		for i in range(N_final):
			for j in range(i+1,N_final):
				connected= self.are_mask_connected(masks_merged[i],masks_merged[j])
				same_class= (class_ids_merged[i]==class_ids_merged[j])
				mergeable= connected
				if mergeable:
					logger.debug("Merged mask (%d,%d) have connected components and are selected for final selection..." % (i,j))
					g_final.addEdge(i,j)

		cc_final = g_final.connectedComponents()
		

		for i in range(len(cc_final)):
			if not cc_final[i]:
				continue
		
			score_best= 0
			index_best= -1
			class_id_best= 0
			n_overlapped= len(cc_final[i])

			for j in range(n_overlapped):
				index= cc_final[i][j]
				mask= masks_merged[index]
				class_id= class_ids_merged[index]
				score= scores_merged[index]
				if score>score_best:	
					score_best= score		
					index_best= index
					class_id_best= class_id
			
			logger.debug("Mask with index %s (score=%f, class=%d) selected as the best among all the overlapping masks..." % (index_best,score_best,class_id_best))
			self.masks_final.append(masks_merged[index_best])
			self.class_ids_final.append(class_ids_merged[index_best])
			self.scores_final.append(scores_merged[index_best])
		
		logger.info("#%d masks finally selected..." % len(self.masks_final))

		# - Compute bounding boxes & image captions from selected masks
		for i in range(len(self.masks_final)):
			mask= self.masks_final[i]
			height= mask.shape[0]
			width= mask.shape[1]
			mask_expanded = np.zeros([height,width,1],dtype=np.bool)
			mask_expanded[:,:,0]= mask
			bbox= utils.extract_bboxes(mask_expanded)
			self.bboxes.append(bbox[0])
	
			label= self.class_names[self.class_ids_final[i]]
			score= self.scores_final[i]
			caption = "{} {:.3f}".format(label, score)
			self.captions.append(caption)


	# ============================
	# ==   COMPUTE PERFORMANCES
	# ============================
	def compute_performances(self):
		""" Compute performances """

		# - Reset matrix
		self.confusion_matrix= np.zeros((self.n_classes,self.n_classes))
		self.confusion_matrix_norm= np.zeros((self.n_classes,self.n_classes))	
		self.purity= np.zeros((1,self.n_classes))
		self.nobjs_true= np.zeros((1,self.n_classes))
		self.nobjs_det= np.zeros((1,self.n_classes))
		self.nobjs_det_right= np.zeros((1,self.n_classes))

		# - Loop over gt boxes and find associations to det boxes
		for i in range(len(self.bboxes_gt)):
			bbox_gt= self.bboxes_gt[i]
			class_id_gt= self.class_ids_gt_merged[i]
			self.nobjs_true[0][class_id_gt]+= 1
			
			# - Find associations between true and detected objects according to largest IOU
			index_best= -1
			iou_best= 0
			logger.debug("len(self.bboxes)=%d, len(self.class_ids_final)=%d" % (len(self.bboxes),len(self.class_ids_final)))
	
			for j in range(len(self.bboxes)):
				class_id= self.class_ids_final[j]
				bbox= self.bboxes[j]
				iou= utils.get_iou(bbox, bbox_gt)
				logger.debug("IOU(det=%d,true=%d)=%f" % (j,i,iou))
				if iou>self.iou_thr and iou>=iou_best:
					index_best= j
					iou_best= iou

			# - Update confusion matrix
			if index_best==-1:
				logger.info("True object no. %d (class_id=%d) not associated to any detected object ..." % (i+1,class_id_gt))
			else:
				class_id_det= self.class_ids_final[index_best]
				self.confusion_matrix[class_id_gt][class_id_det]+= 1
				logger.info("True object no. %d (class_id=%d) associated to detected object no. %d (class_id=%d) ..." % (i+1,class_id_gt,index_best,class_id_det))
			

		# - Normalize confusion matrix
		for i in range(self.n_classes):
			norm= self.nobjs_true[0][i]
			if norm<=0:
				continue
			for j in range(self.n_classes):
				C= self.confusion_matrix[i][j]
				C_norm= C/norm
				self.confusion_matrix_norm[i][j]= C_norm

		# - Compute purity
		for j in range(len(self.bboxes)):
			bbox= self.bboxes[j]
			class_id= self.class_ids_final[j]
			self.nobjs_det[0][class_id]+= 1

			# - Find association to true box
			index_best= -1
			iou_best= 0
			for i in range(len(self.bboxes_gt)):
				bbox_gt= self.bboxes_gt[i]
				class_id_gt= self.class_ids_gt_merged[i]	
				iou= utils.get_iou(bbox, bbox_gt)
				if iou>self.iou_thr and iou>=iou_best:
					index_best= i
					iou_best= iou
		
			# - Check if correctly detected
			if index_best!=-1:
				class_id_det= self.class_ids_gt_merged[index_best]	
				if class_id==class_id_det:
					self.nobjs_det_right[0][class_id]+= 1

	
		for j in range(self.n_classes):
			if self.nobjs_det[0][j]<=0:
				continue
			p= self.nobjs_det_right[0][j]/self.nobjs_det[0][j]
			self.purity[0][j]= p
	

		# - Print confusion matrix
		print("== SAMPLE NOBJ TRUE ==")
		print(self.nobjs_true)

		print("== SAMPLE NOBJ DET ==")
		print(self.nobjs_det)

		print("== SAMPLE NOBJ DET CORRECTLY ==")
		print(self.nobjs_det_right)

		print("== SAMPLE CLASSIFICATION MATRIX (not normalized) ==")
		print(self.confusion_matrix)

		print("== SAMPLE CLASSIFICATION MATRIX (normalized) ==")
		print(self.confusion_matrix_norm)

		print("== SAMPLE PURITY ==")
		print(self.purity)


	# ========================
	# ==   DRAW RESULTS
	# ========================
	def draw_results(self,outfile):
		""" Draw results """

		# - Create axis
		logger.debug("Create axis...")
		height, width = self.image.shape[:2]
		#figsize=(height,width)
		figsize=(16,16)
		fig, ax = plt.subplots(1, figsize=figsize)
	
		# - Show area outside image boundaries
		logger.debug("Show area outside image boundaries...")
		title= self.image_path_base_noext
		#ax.set_ylim(height + 10, -10)
		#ax.set_xlim(-10, width + 10)
		ax.set_ylim(height + 2, -2)
		ax.set_xlim(-2, width + 2)
		ax.axis('off')
		ax.set_title(title,fontsize=30)
	
		#ax.set_frame_on(False)

		masked_image = self.image.astype(np.uint32).copy()

		# - Draw true bounding box
		if self.bboxes_gt:
			logger.debug("Draw true bounding box...")
			for i in range(len(self.bboxes_gt)):
				label= 'bkg'
				if self.class_ids_gt_merged:
					label= self.class_names[self.class_ids_gt_merged[i]]
				color_gt = self.class_color_map[label]
	
				y1, x1, y2, x2 = self.bboxes_gt[i]
				p = patches.Rectangle((x1, y1), x2 - x1, y2 - y1, linewidth=1,alpha=0.7, linestyle="dashed",edgecolor=color_gt, facecolor='none')
				ax.add_patch(p)

				#caption = captions_gt[i]
				caption = ""
				ax.text(x1, y1 + 8, caption, color='w', size=13, backgroundcolor="none")


		# - Draw detected objects
		if self.masks_final:
			logger.debug("Draw detected objects...")
			for i in range(len(self.masks_final)):
				label= self.class_names[self.class_ids_final[i]]
				color = self.class_color_map[label]
		
				# Bounding box
				y1, x1, y2, x2 = self.bboxes[i]
				p = patches.Rectangle((x1, y1), x2 - x1, y2 - y1, linewidth=2,alpha=0.7, linestyle="solid",edgecolor=color, facecolor='none')
				ax.add_patch(p)
	
				# Label
				caption = self.captions[i]
				ax.text(x1, y1 + 8, caption, color=color, size=20, backgroundcolor="none")

				# Mask
				mask= self.masks_final[i]
				masked_image = visualize.apply_mask(masked_image, mask, color)
	
				# Mask Polygon
				# Pad to ensure proper polygons for masks that touch image edges.
				padded_mask = np.zeros( (mask.shape[0] + 2, mask.shape[1] + 2), dtype=np.uint8)
				padded_mask[1:-1, 1:-1] = mask
				contours = find_contours(padded_mask, 0.5)
				for verts in contours:
					# Subtract the padding and flip (y, x) to (x, y)
					verts = np.fliplr(verts) - 1
					p = Polygon(verts, facecolor="none", edgecolor=color)
					ax.add_patch(p)

			ax.imshow(masked_image.astype(np.uint8))


		# - Write to file	
		logger.debug("Write to file %s ..." % outfile)
		t1 = time.time()
		fig.savefig(outfile)
		#fig.savefig(outfile,bbox_inches='tight')
		t2 = time.time()
		#print('savefig: %.2fs' % (t2 - t1))
		plt.close(fig)
		#plt.show()




	
	# ========================
	# ==     MASK METHODS
	# ========================
	def merge_masks(self,mask1,mask2):
		""" Merge masks """
		mask= mask1 + mask2
		mask[mask>1]= 1	
		return mask

	def extract_mask_connected_components(self,mask):
		""" Extract mask components """
		labels, ncomponents= skimage.measure.label(mask, background=0, return_num=True, connectivity=1)
		return labels, ncomponents


	def are_mask_connected(self,mask1,mask2):
		""" Check if two masks are connected """

		# - Find how many components are found in both masks
		labels1, ncomponents1= self.extract_mask_connected_components(mask1)
		labels2, ncomponents2= self.extract_mask_connected_components(mask2)
	
		# - Merge masks
		mask= self.merge_masks(mask1,mask2)

		# - Find how many components are found in mask sum
		#   If <ncomp1+ncomp2 the masks are not connected 
		labels, ncomponents= self.extract_mask_connected_components(mask)

		if ncomponents==ncomponents1+ncomponents2:
			connected= False
		else:
			connected= True

		return connected



